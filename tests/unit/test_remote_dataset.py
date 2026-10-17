"""The remote Dataset text_bench (T20): registered only in the registry and the resolver.

It is read in place from one remote Parquet file, so the resolver enforces a bounded time slice
and full-text needs an agent index over the slice. No database is needed for these rules.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from psycopg import sql

from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import agent_indexes
from serenedb_log_mcp.corpus.agent_indexes import AgentIndex
from serenedb_log_mcp.corpus.datasets import (
    BASE_CORPUS,
    DATASET_NAMES,
    DATASETS,
    UnknownColumnError,
    relation,
)
from serenedb_log_mcp.evidence import DataScope
from serenedb_log_mcp.scope import ScopeError

SLM = load_settings({})
TB = DATASETS["text_bench"]
SLICE = AgentIndex(
    schema="slm",
    name="agent_idx_text_bench_0a1b2c3d",
    dataset="text_bench",
    time_from=datetime(2025, 9, 23, 0, 10),
    time_to=datetime(2025, 9, 23, 0, 10, 8),
    rows=4_500_000,
)


def text(composable: sql.Composable) -> str:
    return composable.as_string()


def scope(time_from: str | None, time_to: str | None) -> DataScope:
    return DataScope(datasets=["text_bench"], time_from=time_from, time_to=time_to)


@pytest.fixture
def slice_index() -> Iterator[AgentIndex]:
    agent_indexes.clear()
    agent_indexes.register(SLICE)
    yield SLICE
    agent_indexes.clear()


def test_text_bench_is_registered_but_not_part_of_the_base_corpus() -> None:
    assert "text_bench" in DATASET_NAMES
    assert BASE_CORPUS == ("bgl", "openstack")
    assert not TB.in_base_corpus
    assert DATASETS["bgl"].in_base_corpus


def test_text_bench_is_remote_and_has_no_full_index() -> None:
    assert TB.remote is not None
    assert TB.index is None
    assert TB.remote.max_scan_span == timedelta(seconds=60)
    assert TB.max_index_rows == 5_000_000
    assert DATASETS["bgl"].remote is None
    assert DATASETS["bgl"].max_index_rows is None  # index_admin.MAX_SLICE_ROWS


def test_it_resolves_to_the_view_and_its_facts_in_slm_schema() -> None:
    rel = relation("text_bench", settings=SLM)

    assert text(rel.table) == '"slm"."tb_entry"'
    assert text(rel.facts_table) == '"slm"."tb_facts"'
    assert rel.index_name == ""


def test_canonical_names_inside_the_view_and_its_concepts() -> None:
    assert TB.table_columns == ("ts", "component", "level", "message", "trace_id", "span_id")
    rel = relation("text_bench", settings=SLM)
    assert rel.columns["time"] == "ts"
    assert rel.columns["source"] == "component"
    assert rel.columns["trace"] == "trace_id"
    with pytest.raises(UnknownColumnError, match="no column 'pattern'"):
        rel.column("pattern")


@pytest.mark.parametrize(
    ("time_from", "time_to"),
    [(None, None), ("2025-09-23T00:10:00", None), (None, "2025-09-23T00:10:00")],
)
def test_an_unbounded_scope_is_refused_with_a_slice_and_create_index(
    time_from: str | None, time_to: str | None
) -> None:
    with pytest.raises(ScopeError) as excinfo:
        relation("text_bench", "engineer", scope(time_from, time_to), settings=SLM)

    message = str(excinfo.value)
    assert "1,000,000,000" in message
    assert "60 seconds" in message
    assert "time_from='2025-09-23T00:10:00', time_to='2025-09-23T00:10:10'" in message
    assert "create_index(dataset='text_bench'" in message
    assert "5,000,000" in message


def test_a_slice_longer_than_the_scan_span_is_refused_with_its_length() -> None:
    with pytest.raises(ScopeError) as excinfo:
        relation("text_bench", scope=scope("2025-09-23T00:10:00", "2025-09-23T00:15:00"), settings=SLM)

    message = str(excinfo.value)
    assert "is 5 minutes long" in message
    assert "at most 60 seconds" in message


def test_a_bounded_slice_resolves_and_reads_the_view() -> None:
    rel = relation("text_bench", scope=scope("2025-09-23T00:10:00", "2025-09-23T00:11:00"), settings=SLM)

    assert text(rel.table) == '"slm"."tb_entry"'
    assert rel.agent_index is None


def test_without_a_scope_the_slice_rule_is_left_to_the_caller() -> None:
    # Statements that read beyond the Data Scope (facts, baselines, index builds) bound their own reads.
    assert relation("text_bench", settings=SLM).dataset is TB


def test_full_text_without_an_agent_index_asks_for_one() -> None:
    rel = relation("text_bench", scope=scope("2025-09-23T00:10:00", "2025-09-23T00:10:10"), settings=SLM)

    with pytest.raises(ScopeError) as excinfo:
        _ = rel.index

    message = str(excinfo.value)
    assert "full-text matching (query) needs an agent index" in message
    assert "create_index(dataset='text_bench'" in message
    assert "Without query" in message


def test_a_slice_inside_an_agent_index_reads_it(slice_index: AgentIndex) -> None:
    rel = relation("text_bench", scope=scope("2025-09-23T00:10:01", "2025-09-23T00:10:05"), settings=SLM)

    with agent_indexes.capture_use() as used:
        assert text(rel.index) == '"slm"."agent_idx_text_bench_0a1b2c3d"'
    assert used == [slice_index]
    assert text(rel.table) == '"slm"."tb_entry"'


def test_a_slice_reaching_past_the_agent_index_still_needs_one(slice_index: AgentIndex) -> None:
    rel = relation("text_bench", scope=scope("2025-09-23T00:10:01", "2025-09-23T00:10:20"), settings=SLM)

    with pytest.raises(ScopeError, match="create_index"):
        _ = rel.index


def test_the_routing_note_says_the_index_is_a_snapshot_refreshed_by_reindex() -> None:
    note = SLICE.routing_note()

    assert "snapshot" in note
    assert "REINDEX INDEX slm.agent_idx_text_bench_0a1b2c3d" in note
    assert "full index;" not in note.replace("has no full index", "")


def test_the_remote_datasets_of_the_agent_index_module_match_the_registry() -> None:
    assert {name for name, entry in DATASETS.items() if entry.remote} == agent_indexes.REMOTE_DATASETS
    for name in DATASET_NAMES:
        assert agent_indexes.is_agent_name(agent_indexes.new_name(name))
