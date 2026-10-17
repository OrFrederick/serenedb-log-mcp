"""find_patterns (T26) without a database: the embedding text, the query vector parameter, the one
SQL statement, the rank fusion, and the errors when the optional embedder is missing."""

from __future__ import annotations

import builtins
import math
from typing import Any

import anyio
import pytest
from fake_embedder import FakeEmbedder
from mcp import Client

from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import embeddings
from serenedb_log_mcp.corpus.datasets import relation
from serenedb_log_mcp.corpus.embeddings import EmbeddingUnavailable, FastEmbedder, document_text
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.tools._catalogue import COLUMNS, vector_literal, vector_statement
from serenedb_log_mcp.tools.find_patterns import RESULT_COLUMNS, RRF_K, _ranked

SETTINGS = load_settings({}, slm_schema="slm")
BGL = relation("bgl", settings=SETTINGS)


def statement_row(
    template_id: str, distance: float | None, bm25: float | None, entries: int = 1
) -> list[Any]:
    return [template_id, "KERNEL", "INFO", entries, None, None, None, f"text {template_id}", distance, bm25]


def call_error(**arguments: Any) -> str:
    async def one() -> str:
        async with Client(create_server(), mode="legacy") as client:
            result = await client.call_tool("find_patterns", arguments)
            assert result.is_error
            return str(result.content[0].text)

    return anyio.run(one)


# The embedded text ---------------------------------------------------------------------------------


def test_the_document_text_is_the_component_and_the_template_without_masks() -> None:
    assert document_text("KERNEL", "<*> double-hummer alignment exceptions") == (
        "KERNEL: double-hummer alignment exceptions"
    )
    assert document_text(None, "ciod: Error loading <*>: program image too big, <*> > <*>") == (
        "ciod: Error loading : program image too big, >"
    )


def test_the_query_vector_is_a_compact_literal_with_four_decimals() -> None:
    literal = vector_literal([0.123456, -0.5, 0.0, 1e-7])

    assert literal == "[0.1235,-0.5,0,0]"
    assert " " not in vector_literal(FakeEmbedder().embed_query("memory error"))


# The statement -------------------------------------------------------------------------------------


def test_one_statement_scans_the_vectors_and_left_joins_the_bm25_matches() -> None:
    statement = vector_statement(BGL, "[1,0]", "m", 384, ["memory"], ["ddr"])
    text = statement.text

    assert text.count("SELECT") == 4  # vec, hits, catalogue and the outer select
    assert "emb <=> %s::FLOAT[384]" in text
    assert '"slm"."pattern_vector"' in text
    assert '"slm"."pattern_idx"' in text
    assert "LEFT JOIN hits" in text
    assert "ORDER BY" not in text  # BM25 sorts with a tie-break crash SereneDB 26.10.1 (#2)
    assert statement.params[:3] == ("[1,0]", "bgl", "m")
    assert text.count("%s") == len(statement.params)


def test_semantic_mode_has_no_bm25_side() -> None:
    statement = vector_statement(BGL, "[1,0]", "m", 384)

    assert "hits" not in statement.text
    assert "BM25" not in statement.text
    assert "NULL AS score" in statement.text


# Ranking -------------------------------------------------------------------------------------------


def test_hybrid_fuses_the_vector_and_the_bm25_ranks() -> None:
    rows = [
        statement_row("a", 0.10, None),  # vector 1, no word match
        statement_row("b", 0.20, 5.0),  # vector 2, lexical 1
        statement_row("c", 0.30, 1.0),  # vector 3, lexical 2
    ]

    ranked = _ranked(rows, "hybrid")

    assert [r[0] for r in ranked] == ["b", "c", "a"]
    by_id = {r[0]: dict(zip(RESULT_COLUMNS, r, strict=True)) for r in ranked}
    assert by_id["b"]["score"] == round(1 / (RRF_K + 2) + 1 / (RRF_K + 1), 6)
    assert by_id["a"]["score"] == round(1 / (RRF_K + 1), 6)
    assert (by_id["a"]["vector_rank"], by_id["a"]["lexical_rank"]) == (1, None)
    assert by_id["c"]["similarity"] == 0.7


def test_semantic_ranks_by_similarity_alone() -> None:
    rows = [statement_row("a", 0.30, 9.0), statement_row("b", 0.10, None)]

    ranked = _ranked(rows, "semantic")

    assert [r[0] for r in ranked] == ["b", "a"]
    assert [r[len(COLUMNS)] for r in ranked] == [0.9, 0.7]


def test_equal_bm25_scores_rank_by_entries_then_template_id_like_search_patterns() -> None:
    rows = [
        statement_row("x", 0.5, 2.0, entries=1),
        statement_row("y", 0.6, 2.0, entries=9),
    ]

    ranked = {r[0]: r[-1] for r in _ranked(rows, "hybrid")}

    assert ranked == {"y": 1, "x": 2}


# The embedder --------------------------------------------------------------------------------------


def test_the_fake_embedder_returns_unit_vectors_of_the_model_size() -> None:
    vector = FakeEmbedder().embed_query("instruction cache parity error")

    assert len(vector) == embeddings.DIMENSIONS
    assert math.isclose(sum(v * v for v in vector), 1.0)


def test_without_fastembed_the_embedder_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def no_fastembed(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "fastembed":
            raise ImportError("No module named 'fastembed'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_fastembed)

    with pytest.raises(EmbeddingUnavailable, match=r"uv sync --extra vectors"):
        FastEmbedder()


def test_the_model_cache_follows_fastembed_cache_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv(embeddings.CACHE_ENV, str(tmp_path))
    assert embeddings.cache_dir() == tmp_path
    monkeypatch.delenv(embeddings.CACHE_ENV)
    assert embeddings.cache_dir().parts[-2:] == ("serenedb-log-mcp", "fastembed")


def test_the_tool_fails_with_the_setup_steps_when_the_embedder_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> embeddings.Embedder:
        raise EmbeddingUnavailable(f"fastembed is not installed. {embeddings.INSTALL_HINT}")

    embeddings.set_embedder(None)
    monkeypatch.setattr(embeddings, "_default", unavailable)

    error = call_error(role="engineer", dataset="bgl", text="memory errors")

    assert "uv sync --extra vectors" in error
    assert "make embed-patterns" in error
    assert "list_patterns(dataset='bgl')" in error
    assert "search_patterns" in error


def test_an_empty_text_is_a_scope_error() -> None:
    error = call_error(role="engineer", dataset="openstack", text="   ")

    assert "text is empty" in error


# The description ----------------------------------------------------------------------------------


def test_the_description_says_how_it_differs_from_the_other_catalogue_tools() -> None:
    tools = {tool.name: tool for tool in anyio.run(create_server().list_tools)}
    description = tools["find_patterns"].description or ""

    assert "list_patterns" in description
    assert "search_patterns" in description
    assert "Optional" in description
    assert "make embed-patterns" in description
    assert "always returns" in description


# Review fixes (#102) -------------------------------------------------------------------------------


def test_a_pattern_without_a_vector_is_ranked_by_its_word_match_in_hybrid_mode() -> None:
    rows = [statement_row("a", 0.10, None), statement_row("new", None, 3.0)]

    hybrid = {r[0]: dict(zip(RESULT_COLUMNS, r, strict=True)) for r in _ranked(rows, "hybrid")}
    semantic = [r[0] for r in _ranked(rows, "semantic")]

    assert hybrid["new"]["vector_rank"] is None
    assert hybrid["new"]["similarity"] is None
    assert hybrid["new"]["lexical_rank"] == 1
    assert hybrid["new"]["score"] == round(1 / (RRF_K + 1), 6)
    assert semantic == ["a"]


def test_a_pattern_with_neither_a_vector_nor_a_word_match_is_left_out() -> None:
    rows = [statement_row("a", 0.10, None), statement_row("none", None, None)]

    assert [r[0] for r in _ranked(rows, "hybrid")] == ["a"]


def test_bm25_scores_are_rounded_before_ranking_like_search_patterns() -> None:
    # Equal at four decimals, so entries decide, as in search_patterns.
    rows = [statement_row("x", 0.5, 2.00001, entries=1), statement_row("y", 0.6, 2.00002, entries=9)]

    assert {r[0]: r[-1] for r in _ranked(rows, "hybrid")} == {"y": 1, "x": 2}


def test_the_statement_is_driven_by_the_catalogue() -> None:
    text = vector_statement(BGL, "[1,0]", "m", 384, ["memory"]).text

    assert "FROM catalogue AS c LEFT JOIN vec AS v" in text
    assert "LEFT JOIN hits AS h ON h.template_id = c.template_id" in text


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_non_finite_query_vector_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="NaN or infinite"):
        vector_literal([0.1, bad])


def test_a_text_without_words_names_the_text_parameter() -> None:
    error = call_error(role="engineer", dataset="bgl", text="?!")

    assert "find_patterns: text='?!' has no words" in error
