"""scripts/otel_setup.py and sql/remote/otel_demo.sql (T21), without a database."""

from __future__ import annotations

import re

import pytest

import otel_setup
from derive import render, split_statements
from serenedb_log_mcp.config import OTEL_DEMO_FILES, load_settings
from serenedb_log_mcp.corpus.datasets import OTEL_COLUMNS, SIGNAL_TABLES

FILES = load_settings({}, otel_demo_url="https://h/otel_demo").otel_demo_files()


def statements() -> list[str]:
    return split_statements(render(otel_setup.SETUP_SQL.read_text(), otel_setup.variables("huge", FILES)))


def view(name: str) -> str:
    return next(s for s in statements() if s.startswith(f'CREATE VIEW "huge".{name} AS'))


def test_every_signal_file_is_one_quoted_placeholder() -> None:
    values = otel_setup.variables("huge", {**FILES, "logs": "https://h/it's.parquet"})

    assert values["slm"] == '"huge"'
    assert values["logs"] == "'https://h/it''s.parquet'"
    assert set(values) == {"slm", *OTEL_DEMO_FILES}


def test_each_view_reads_exactly_one_file() -> None:
    text = otel_setup.SETUP_SQL.read_text()

    for signal in OTEL_DEMO_FILES:
        assert text.count(f"read_parquet(${{{signal}}})") == 1
    assert "*" not in "".join(re.findall(r"read_parquet\([^)]*\)", text))


def test_the_log_view_has_the_registry_columns_and_a_cheap_time_column() -> None:
    log = view("otel_log")

    assert "read_parquet('https://h/otel_demo/otel_logs.parquet')" in log
    for column in OTEL_COLUMNS:
        assert f" AS {column}" in log
    assert 'CAST("TimestampTime" AS TIMESTAMP) AS ts,' in log
    assert 'CAST("Timestamp" AS TIMESTAMP)     AS ts_exact' in log


def test_the_span_view_names_what_get_trace_reads() -> None:
    spans = view("otel_span")

    assert "read_parquet('https://h/otel_demo/otel_traces.parquet')" in spans
    for column in (
        "ts",
        "trace_id",
        "span_id",
        "parent_span_id",
        "service",
        "operation",
        "kind",
        "duration_ns",
        "status",
        "status_message",
    ):
        assert f" AS {column}" in spans


def test_every_signal_table_of_the_registry_is_created() -> None:
    created = " ".join(s.split(" AS ")[0] for s in statements() if s.startswith("CREATE"))

    for table in SIGNAL_TABLES["otel_demo"].values():
        assert f'"huge".{table}' in created
    assert '"huge".otel_facts' in created


def test_views_are_dropped_and_created_so_a_changed_column_type_never_blocks_a_rerun() -> None:
    for name in ("otel_log", "otel_span", "otel_metric_gauge", "otel_metric_sum", "otel_metric_histogram"):
        assert f'DROP VIEW IF EXISTS "huge".{name}' in statements()


def test_the_facts_take_the_range_from_the_footer_of_the_second_column() -> None:
    facts = next(s for s in statements() if s.startswith('CREATE OR REPLACE TABLE "huge".otel_facts'))

    assert "parquet_metadata('https://h/otel_demo/otel_logs.parquet')" in facts
    assert "path_in_schema = 'TimestampTime'" in facts


def test_the_metric_roll_up_takes_increases_of_cumulative_series() -> None:
    minute = next(
        s for s in statements() if s.startswith('CREATE OR REPLACE TABLE "huge".otel_metric_minute')
    )

    assert "lag(last_value) OVER (PARTITION BY series ORDER BY minute)" in minute
    assert "WHERE increase >= 0" in minute
    assert "service <> ''" in minute
    baseline = next(
        s for s in statements() if s.startswith('CREATE OR REPLACE TABLE "huge".otel_metric_baseline')
    )
    assert "quantile_cont(value, 0.05)" in baseline
    assert "median(value)" in baseline


def test_the_warm_up_probes_a_trace_id_inside_every_row_group_range() -> None:
    # An id below every row group's minimum is pruned by min/max statistics without reading a bloom
    # filter, so it would warm nothing.
    probes = otel_setup.warm_statements("huge")

    assert len(probes) == 2
    assert all(otel_setup.WARM_TRACE_ID in sql for sql, _ in probes)
    assert {sql.split(" FROM ")[1].split(" ")[0] for sql, _ in probes} == {
        '"huge"."otel_span"',
        '"huge"."otel_log"',
    }
    assert otel_setup.WARM_TRACE_ID.startswith("7fff")


def test_a_glob_directory_is_refused_before_connecting(capsys: pytest.CaptureFixture[str]) -> None:
    assert otel_setup.main(["--url", "https://h/otel_demo/*"]) == 2
    assert "no glob" in capsys.readouterr().err


def test_the_remote_sql_reads_no_labels_and_no_other_files() -> None:
    text = otel_setup.SETUP_SQL.read_text().lower()

    assert "abnormal" not in text
    for reader in ("read_csv", "read_text", "read_json"):
        assert reader not in text
