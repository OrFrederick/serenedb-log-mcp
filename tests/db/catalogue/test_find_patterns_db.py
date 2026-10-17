"""`find_patterns` against a real SereneDB (T26), on the Loghub 2k samples derived into a throwaway
schema whose Pattern vectors `derived_sample` stored with the fake embedder (tests/support/
fake_embedder.py), which the tests also query with. The ranks are checked against cosine
similarities computed here from the stored vectors and against search_patterns' own ranking."""

from __future__ import annotations

import math
import uuid
from typing import Any

import psycopg
import pytest
from aggregation_tools import call, call_error, row_dicts
from fake_embedder import MODEL, FakeEmbedder
from psycopg import sql

import embed_patterns
from serenedb_log_mcp import config
from serenedb_log_mcp.config import get_settings, load_settings
from serenedb_log_mcp.corpus import embeddings
from serenedb_log_mcp.corpus.embeddings import document_text
from serenedb_log_mcp.tools._catalogue import COLUMNS

DATASETS = ("bgl", "openstack")


def stored(schema: str, dataset: str) -> dict[str, list[float]]:
    """The stored vectors of the fake model, by template_id."""
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        rows = conn.execute(
            sql.SQL("SELECT template_id, emb FROM {} WHERE dataset = %s AND model = %s").format(
                sql.Identifier(schema, "pattern_vector")
            ),
            [dataset, MODEL],
        ).fetchall()
    return {str(tid): [float(v) for v in emb] for tid, emb in rows}


def patterns(schema: str, dataset: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        return [
            tuple(r)
            for r in conn.execute(
                sql.SQL(
                    "SELECT template_id, component, template_text, template_source FROM {} WHERE dataset = %s"
                ).format(sql.Identifier(schema, "pattern")),
                [dataset],
            ).fetchall()
        ]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


@pytest.mark.parametrize("dataset", DATASETS)
def test_every_pattern_with_a_text_has_its_vector(sample: str, dataset: str) -> None:
    found = patterns(sample, dataset)
    vectors = stored(sample, dataset)
    fake = FakeEmbedder()

    with_text = {tid for tid, _, _, source in found if source != "empty"}
    assert set(vectors) == with_text
    for tid, component, text, source in found:
        if source != "empty":
            expected = fake.embed_documents([document_text(component, text)])[0]
            assert cosine(vectors[tid], expected) == pytest.approx(1.0, abs=1e-5)


@pytest.mark.parametrize("dataset", DATASETS)
def test_semantic_mode_ranks_by_the_cosine_similarity_serenedb_computes(sample: str, dataset: str) -> None:
    text = "instance error" if dataset == "openstack" else "kernel error"
    result = call("find_patterns", role="engineer", dataset=dataset, text=text, mode="semantic", top=50)

    query = FakeEmbedder().embed_query(text)
    expected = {tid: cosine(vector, query) for tid, vector in stored(sample, dataset).items()}
    rows = row_dicts(result)
    assert result["columns"] == [*COLUMNS, "score", "similarity", "vector_rank", "lexical_rank"]
    assert result["total_matches"] == result["summary"]["patterns_total"] == len(expected)
    assert result["summary"]["lexical_matches"] is None
    for row in rows:
        # The query vector is bound with four decimals.
        assert row["similarity"] == pytest.approx(expected[row["template_id"]], abs=2e-3)
        assert row["score"] == row["similarity"]
        assert row["lexical_rank"] is None
    assert [r["vector_rank"] for r in rows] == list(range(1, len(rows) + 1))
    similarities = [r["similarity"] for r in rows]
    assert similarities == sorted(similarities, reverse=True)


def test_hybrid_mode_carries_search_patterns_ranking(sample: str) -> None:
    hybrid = call("find_patterns", role="engineer", dataset="openstack", text="spawning instance", top=50)
    lexical = call("search_patterns", role="engineer", dataset="openstack", query="spawning instance", top=50)

    lexical_order = [r["template_id"] for r in row_dicts(lexical)]
    ranks = {r["template_id"]: r["lexical_rank"] for r in row_dicts(hybrid) if r["lexical_rank"] is not None}
    assert hybrid["summary"]["lexical_matches"] == lexical["summary"]["patterns_total"] == len(ranks)
    assert sorted(ranks, key=ranks.__getitem__) == lexical_order
    assert hybrid["summary"]["words"] == lexical["summary"]["words"]
    assert hybrid["summary"]["synonyms"] == lexical["summary"]["synonyms"]
    scores = [r["score"] for r in row_dicts(hybrid)]
    assert scores == sorted(scores, reverse=True)
    assert len(hybrid["sql"]) == 1


def test_top_truncates_with_a_note_and_keeps_the_total(sample: str) -> None:
    result = call("find_patterns", role="team_lead", dataset="bgl", text="error", top=3)

    assert result["returned"] == 3
    assert result["truncated"] is True
    assert f"Showing the 3 best of {result['total_matches']:,} ranked Patterns" in result["notes"][0]


def test_vectors_of_another_model_are_an_actionable_error(sample: str) -> None:
    class Other(FakeEmbedder):
        model = "test/another-model"

    embeddings.set_embedder(Other())

    error = call_error("find_patterns", role="engineer", dataset="bgl", text="error")

    assert "No Pattern vectors of test/another-model" in error
    assert "make embed-patterns" in error


def test_a_schema_without_the_vector_table_gets_an_actionable_error(
    sample: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = f"scratch_t26_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(empty)))
    try:
        monkeypatch.setattr(config, "_active", load_settings(slm_schema=empty))
        error = call_error("find_patterns", role="engineer", dataset="bgl", text="error")
        assert "make embed-patterns" in error
        assert "make derive" in error
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(empty)))


def test_embedding_again_replaces_the_models_rows(sample: str) -> None:
    before = {d: stored(sample, d) for d in DATASETS}

    assert embed_patterns.main(["--schema", sample], embedder=FakeEmbedder()) == 0

    assert {d: stored(sample, d) for d in DATASETS} == before
