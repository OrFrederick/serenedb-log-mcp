"""count_by and trend on the remote Dataset text_bench (T20), without a database.

The tool modules are unchanged; the shared helper `_aggregate` reads a remote Dataset only over a
bounded, contiguous time range: the Data Scope plus its previous period when that stays within the
Dataset's scan span (an OR of two ranges defeats Parquet row-group pruning), and never the whole
Dataset for the weekly baseline.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest

from serenedb_log_mcp.config import Role
from serenedb_log_mcp.corpus import agent_indexes
from serenedb_log_mcp.corpus.agent_indexes import AgentIndex
from serenedb_log_mcp.corpus.facts import DatasetFacts
from serenedb_log_mcp.scope import ScopeError
from serenedb_log_mcp.tools import _aggregate
from serenedb_log_mcp.tools._aggregate import (
    Prepared,
    choose_datasets,
    prepare,
    rank_statement,
    stats_statement,
)
from serenedb_log_mcp.tools.trend import bucket_statement

FIRST, LAST = datetime(2025, 9, 23), datetime(2025, 9, 23, 0, 29, 24, 39037)
KNOWN = DatasetFacts(
    "text_bench", 1_000_000_000, FIRST, LAST, {"frontend": 9, "checkout": 3}, {"error": 5, "info": 7}, {}
)


def index(start: datetime, end: datetime) -> AgentIndex:
    return AgentIndex("slm", "agent_idx_text_bench_0a1b2c3d", "text_bench", start, end, 4_000_000)


@pytest.fixture(autouse=True)
def facts(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(_aggregate, "dataset_facts", lambda name, role: KNOWN)
    agent_indexes.clear()
    yield
    agent_indexes.clear()


def call(time_from: str | None, time_to: str | None, **kwargs: Any) -> Prepared:
    arguments: dict[str, Any] = {
        "group_by": "component",
        "query": None,
        "components": None,
        "levels": None,
        "sources": None,
        "alert_categories": None,
        "pattern_ids": None,
        "family_id": None,
    }
    arguments.update(kwargs)
    return prepare(Role.TEAM_LEAD, ["text_bench"], time_from=time_from, time_to=time_to, **arguments)


def stats_text(prepared: Prepared) -> tuple[str, tuple[Any, ...]]:
    statement = stats_statement(prepared.parts, {"text_bench": ["frontend"]}, {"text_bench": ["frontend"]})
    return statement.text, statement.params


def test_a_call_that_names_no_dataset_reads_only_the_base_corpus() -> None:
    assert choose_datasets(None, "component", None) == (["bgl", "openstack"], [])


def test_an_unbounded_call_on_text_bench_suggests_a_slice_or_create_index() -> None:
    with pytest.raises(ScopeError) as excinfo:
        call(None, None)

    assert "create_index" in str(excinfo.value)
    assert "time_from" in str(excinfo.value)


def test_a_short_slice_reads_the_slice_and_its_previous_period_as_one_range() -> None:
    prepared = call("2025-09-23T00:10:00", "2025-09-23T00:10:10")
    span = prepared.parts[0].window

    assert span.previous == (datetime(2025, 9, 23, 0, 9, 50), datetime(2025, 9, 23, 0, 10))
    text, params = stats_text(prepared)
    assert '"tb_entry"' in text
    # The baseline reads [previous start, scope end) and nothing outside it.
    assert datetime(2025, 9, 23, 0, 9, 50) in params
    assert params.count(datetime(2025, 9, 23, 0, 10, 10)) >= 2
    assert " OR " not in text.split("FROM", 1)[1].split("cur AS", 1)[0]


def test_the_previous_period_is_dropped_when_it_would_double_the_read_past_the_span() -> None:
    prepared = call("2025-09-23T00:10:00", "2025-09-23T00:10:40")
    span = prepared.parts[0].window

    assert span.previous is None
    assert span.previous_problem is not None
    assert "60 seconds" in span.previous_problem
    _, params = stats_text(prepared)
    assert datetime(2025, 9, 23, 0, 9, 20) not in params


def test_the_weekly_baseline_never_reads_the_whole_remote_dataset() -> None:
    text, params = stats_text(call("2025-09-23T00:10:00", "2025-09-23T00:10:10"))

    where = text.split("cur AS", 1)[0].split(" WHERE ", 1)[1]
    # The matching rows always carry the time bound, so the remote scan is pruned to the range.
    assert 'e."ts" >= %s AND e."ts" < %s' in where
    bound_at = text.split(" WHERE ", 1)[0].count("%s")
    assert params[bound_at : bound_at + 2] == (
        datetime(2025, 9, 23, 0, 9, 50),
        datetime(2025, 9, 23, 0, 10, 10),
    )


def test_a_base_corpus_baseline_still_reads_the_whole_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    bgl = DatasetFacts("bgl", 10, datetime(2005, 6, 3), datetime(2006, 1, 4), {"KERNEL": 1}, {"INFO": 1}, {})
    monkeypatch.setattr(_aggregate, "dataset_facts", lambda name, role: bgl)
    prepared = prepare(
        Role.TEAM_LEAD, ["bgl"], group_by="component", query=None, time_from="2005-08-01",
        time_to="2005-08-08", components=None, levels=None, sources=None, alert_categories=None,
        pattern_ids=None, family_id=None,
    )  # fmt: skip
    statement = stats_statement(prepared.parts, {"bgl": ["KERNEL"]}, {"bgl": ["KERNEL"]})

    where = statement.text.split("cur AS", 1)[0].split(" WHERE ", 1)[1]
    assert where.strip().startswith("TRUE")


def test_full_text_on_text_bench_without_an_agent_index_asks_for_one() -> None:
    prepared = call("2025-09-23T00:10:00", "2025-09-23T00:10:10", query="visa")

    with pytest.raises(ScopeError, match="create_index"):
        rank_statement(prepared.parts, 5, show_single_hidden=True)


def test_full_text_inside_an_agent_index_reads_it_with_the_previous_period_when_it_holds_both() -> None:
    agent_indexes.register(index(datetime(2025, 9, 23, 0, 9, 50), datetime(2025, 9, 23, 0, 10, 10)))
    prepared = call("2025-09-23T00:10:00", "2025-09-23T00:10:10", query="visa")

    rank = rank_statement(prepared.parts, 5, show_single_hidden=True)
    bucket = bucket_statement(prepared.parts[0], ["frontend"], "minute")
    text, _ = stats_text(prepared)
    for statement in (rank.text, bucket.text, text):
        assert '"agent_idx_text_bench_0a1b2c3d"' in statement
    assert prepared.parts[0].window.previous is not None


def test_full_text_drops_the_previous_period_the_agent_index_does_not_hold() -> None:
    agent_indexes.register(index(datetime(2025, 9, 23, 0, 10), datetime(2025, 9, 23, 0, 10, 10)))
    prepared = call("2025-09-23T00:10:00", "2025-09-23T00:10:10", query="visa")

    span = prepared.parts[0].window
    assert span.previous is None
    assert span.previous_problem is not None
    assert "agent index" in span.previous_problem
    text, _ = stats_text(prepared)
    assert '"agent_idx_text_bench_0a1b2c3d"' in text
