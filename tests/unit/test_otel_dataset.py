"""The remote Dataset otel_demo (T21): logs, traces and metrics of one OTel demo run, in the registry.

otel_demo is one Dataset (GLOSSARY: "One named log source ... e.g. ... OTel demo"): its Log Entries
are the OTel logs, and its traces and metrics are signals of the same run, joined on TraceId, that
the resolver names next to the Log Entry view. No database is needed for these rules.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from psycopg import sql

from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus.datasets import (
    BASE_CORPUS,
    CANONICAL_TABLES,
    DATASET_NAMES,
    DATASETS,
    UnknownSignalError,
    check_remote_slice,
    relation,
)
from serenedb_log_mcp.evidence import DataScope
from serenedb_log_mcp.scope import ScopeError

SLM = load_settings({})
OTEL = DATASETS["otel_demo"]


def text(composable: sql.Composable) -> str:
    return composable.as_string()


def test_otel_demo_is_registered_but_not_part_of_the_base_corpus() -> None:
    assert "otel_demo" in DATASET_NAMES
    assert BASE_CORPUS == ("bgl", "openstack")
    assert not OTEL.in_base_corpus


def test_otel_demo_is_remote_without_a_full_index() -> None:
    assert OTEL.remote is not None
    assert OTEL.index is None
    assert OTEL.remote.setup_command == "make otel-setup"


def test_the_log_entries_resolve_to_the_view_and_its_facts() -> None:
    rel = relation("otel_demo", settings=SLM)

    assert text(rel.table) == '"slm"."otel_log"'
    assert text(rel.facts_table) == '"slm"."otel_facts"'
    assert text(relation("text_bench", settings=SLM).facts_table) == '"slm"."tb_facts"'


def test_the_view_has_canonical_columns_and_a_cheap_time_column() -> None:
    assert OTEL.table_columns == ("ts", "ts_exact", "component", "level", "message", "trace_id", "span_id")
    rel = relation("otel_demo", settings=SLM)
    assert text(rel.column("time")) == '"ts"'
    assert text(rel.column("source")) == '"component"'
    assert text(rel.column("trace")) == '"trace_id"'
    assert text(rel.column("span")) == '"span_id"'


@pytest.mark.parametrize(
    ("signal", "table"),
    [
        ("spans", "otel_span"),
        ("metrics_gauge", "otel_metric_gauge"),
        ("metrics_sum", "otel_metric_sum"),
        ("metrics_histogram", "otel_metric_histogram"),
        ("metric_minutes", "otel_metric_minute"),
        ("metric_baselines", "otel_metric_baseline"),
    ],
)
def test_traces_and_metrics_are_signals_of_the_dataset(signal: str, table: str) -> None:
    rel = relation("otel_demo", settings=SLM)

    assert text(rel.signal(signal)) == f'"slm"."{table}"'
    assert table in CANONICAL_TABLES  # so the resolver contract test keeps them out of tool modules


def test_a_dataset_without_the_signal_says_which_one_has_it() -> None:
    with pytest.raises(UnknownSignalError, match="otel_demo"):
        relation("bgl", settings=SLM).signal("spans")
    with pytest.raises(UnknownSignalError, match="metric_minutes"):
        relation("otel_demo", settings=SLM).signal("profiles")


def test_otel_demo_needs_no_time_slice() -> None:
    # The logs file is not sorted by time, so every slice reads the same narrow columns of all row
    # groups: a slice saves nothing, and the resolver does not ask for one.
    whole = DataScope(datasets=["otel_demo"])
    week = DataScope(datasets=["otel_demo"], time_from="2025-05-20T00:00:00", time_to="2025-05-27T00:00:00")

    assert relation("otel_demo", scope=whole, settings=SLM).table_name == "slm.otel_log"
    assert relation("otel_demo", scope=week, settings=SLM).table_name == "slm.otel_log"
    check_remote_slice(OTEL, None, None)
    check_remote_slice(OTEL, datetime(2025, 5, 1), datetime(2025, 6, 1))


def test_text_bench_still_needs_its_slice() -> None:
    with pytest.raises(ScopeError, match="time slice"):
        check_remote_slice(DATASETS["text_bench"], None, None)


def test_full_text_on_otel_demo_explains_why_there_is_no_index() -> None:
    rel = relation("otel_demo", settings=SLM)

    with pytest.raises(ScopeError) as caught:
        _ = rel.index
    message = str(caught.value)
    assert "not sorted by time" in message
    assert "get_trace" in message
    assert "create_index(" not in message  # no advice to build an index that would read 1.8 GB
