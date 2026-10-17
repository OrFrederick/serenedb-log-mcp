"""otel_demo against SereneDB (T21): the setup's views and roll-up, get_trace, and the aggregation tools.

The files are the synthetic ones of conftest.py, written on the server; tests/db/otel/
test_otel_demo_remote.py checks the real files on S3.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
import pytest
from aggregation_tools import call, call_error, row_dicts
from otel_demo_data import SPAN, TRACE, OtelDemo
from psycopg import sql

from serenedb_log_mcp.config import get_settings


def rows(otel: OtelDemo, query: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        statement = sql.SQL(query).format(schema=sql.Identifier(otel.schema))
        return [tuple(row) for row in conn.execute(statement).fetchall()]


def by_signal(result: dict[str, Any], signal: str) -> list[dict[str, Any]]:
    return [row for row in row_dicts(result) if row["signal"] == signal]


# The setup -------------------------------------------------------------------------------------------


def test_the_facts_hold_the_range_and_the_filter_values(otel_demo: OtelDemo) -> None:
    found = {(kind, value): (n, first, last) for kind, value, n, first, last in rows(
        otel_demo, "SELECT kind, value, n, first, last FROM {schema}.otel_facts"
    )}  # fmt: skip

    assert found[("range", None)] == (603, datetime(2025, 5, 25, 0, 0), datetime(2025, 5, 25, 0, 59, 54))
    assert found[("component", "payment")][0] == 2
    assert found[("level", "error")][0] == 122


def test_the_roll_up_turns_cumulative_series_into_per_minute_values(otel_demo: OtelDemo) -> None:
    minute = {
        (metric, kind, m.minute): value
        for metric, kind, m, value in rows(
            otel_demo,
            "SELECT metric, kind, minute, value FROM {schema}.otel_metric_minute WHERE service = 'payment'",
        )
    }

    assert minute[("container.cpu.utilization", "gauge", 10)] == pytest.approx(0.02)
    assert minute[("container.cpu.utilization", "gauge", 30)] == pytest.approx(0.5)
    assert minute[("app.payment.transactions", "counter", 10)] == pytest.approx(10)
    assert minute[("app.payment.transactions", "counter", 30)] == pytest.approx(1)
    assert ("app.payment.transactions", "counter", 0) not in minute  # no earlier value to subtract
    assert minute[("http.client.duration", "histogram", 10)] == pytest.approx(20)
    assert minute[("http.client.duration", "histogram", 30)] == pytest.approx(100)
    assert minute[("jvm.memory.used", "gauge", 30)] == pytest.approx(100)  # non-monotonic sum: a level
    assert rows(otel_demo, "SELECT count(*) FROM {schema}.otel_metric_minute WHERE service = ''") == [(0,)]


def test_every_series_has_a_baseline_of_its_minutes(otel_demo: OtelDemo) -> None:
    found = {
        metric: (minutes, p05, median, p95)
        for metric, minutes, p05, median, p95 in rows(
            otel_demo,
            "SELECT metric, minutes, p05, median, p95 FROM {schema}.otel_metric_baseline "
            "WHERE service = 'payment'",
        )
    }

    assert found["http.client.duration"] == (59, pytest.approx(20), pytest.approx(20), pytest.approx(20))
    assert found["container.cpu.utilization"][0] == 60
    assert found["container.cpu.utilization"][2] == pytest.approx(0.02)


# get_trace ---------------------------------------------------------------------------------------------


def test_get_trace_returns_the_spans_with_self_time_depth_and_the_error_origin(otel_demo: OtelDemo) -> None:
    result = call("get_trace", role="engineer", trace_id=TRACE, pad_minutes=1)

    spans = {row["span_id"]: row for row in by_signal(result, "span")}
    assert len(spans) == 7
    assert next(iter(spans)) == SPAN["L1"]  # by start time
    assert {key: spans[SPAN[key]]["depth"] for key in SPAN} == {
        "L1": 0, "F1": 1, "C1": 2, "C3": 3, "C2": 3, "P1": 4, "P2": 5,
    }  # fmt: skip
    assert {key: spans[SPAN[key]]["self_ms"] for key in SPAN} == {
        "L1": 20.0, "F1": 80.0, "C1": 25.0, "C3": 50.0, "C2": 5.0, "P1": 20.0, "P2": 0.0,
    }  # fmt: skip
    assert spans[SPAN["C1"]]["text"] == "failed to charge card: Visa cache full: cannot add new item."
    summary = result["summary"]
    assert summary["spans_total"] == 7
    assert summary["error_spans"] == 5
    assert [origin["span_id"] for origin in summary["error_origin_spans"]] == [SPAN["P2"]]
    assert summary["slowest_self_span"]["span_id"] == SPAN["F1"]
    assert summary["trace_start"] == "2025-05-25T00:30:00"
    assert summary["trace_end"] == "2025-05-25T00:30:00.200000"
    assert summary["trace_duration_ms"] == 200.0
    assert [entry["service"] for entry in summary["services"]] == [
        "load-generator", "frontend", "checkout", "cart", "payment",
    ]  # fmt: skip


def test_get_trace_returns_the_log_entries_linked_to_their_spans(otel_demo: OtelDemo) -> None:
    result = call("get_trace", role="engineer", trace_id=TRACE)

    logs = by_signal(result, "log")
    assert [(row["service"], row["status"], row["span_id"]) for row in logs] == [
        ("payment", "info", SPAN["P1"]),
        ("payment", "error", SPAN["P1"]),
        ("frontend", "error", SPAN["F1"]),
    ]
    assert logs[1]["text"] == "Visa cache full: cannot add new item."
    assert result["summary"]["log_levels"] == {"info": 1, "error": 2}
    assert result["summary"]["error_log_entries"] == 2


def test_get_trace_flags_the_metrics_that_moved_around_the_trace(otel_demo: OtelDemo) -> None:
    result = call("get_trace", role="engineer", trace_id=TRACE, pad_minutes=1)

    metrics = by_signal(result, "metric")
    assert result["summary"]["metrics_window"] == {"from": "2025-05-25T00:29:00", "to": "2025-05-25T00:32:00"}
    flagged = {(row["service"], row["name"]): row["status"] for row in metrics}
    assert flagged == {
        ("payment", "http.client.duration"): "unusual_high",
        ("payment", "app.payment.transactions"): "unusual_low",
        ("payment", "container.cpu.utilization"): "unusual_high",
        ("payment", "jvm.memory.used"): "normal",
        ("checkout", "container.cpu.utilization"): "normal",
    }
    # Furthest from the median in units of the p05-p95 range, floored at 1% of the median, so the
    # latency of a series that never varied before ranks above the CPU spike.
    assert [row["name"] for row in metrics[:3]] == [
        "http.client.duration", "app.payment.transactions", "container.cpu.utilization",
    ]  # fmt: skip
    latency = metrics[0]
    assert latency["value"] == pytest.approx(1400 / 30, abs=1e-5)  # (200 + 1000 + 200) ms / 30 calls
    assert latency["kind"] == "histogram"
    assert latency["unit"] == "ms"
    assert latency["baseline_median"] == pytest.approx(20)
    assert result["summary"]["metric_series_total"] == 5
    assert result["summary"]["metric_series_unusual"] == 3
    assert result["total_matches"] == 7 + 3 + 5
    assert result["truncated"] is False


def test_get_trace_limits_keep_the_error_origin_and_the_slowest_span(otel_demo: OtelDemo) -> None:
    result = call("get_trace", role="engineer", trace_id=TRACE, span_limit=2, log_limit=1, metric_limit=1)

    assert {row["span_id"] for row in by_signal(result, "span")} == {SPAN["P2"], SPAN["F1"]}
    assert [row["status"] for row in by_signal(result, "log")] == ["error"]
    assert result["truncated"] is True
    assert result["total_matches"] == 7 + 3 + 5
    assert result["returned"] == 4


def test_get_trace_of_an_unknown_trace_is_an_empty_evidence(otel_demo: OtelDemo) -> None:
    result = call("get_trace", role="engineer", trace_id="0" * 32)

    assert result["total_matches"] == 0
    assert any("No span and no Log Entry" in note for note in result["notes"])


def test_get_trace_without_the_setup_says_how_to_set_it_up(
    otel_demo: OtelDemo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from serenedb_log_mcp import config

    monkeypatch.setattr(
        config, "_active", get_settings().model_copy(update={"slm_schema": "scratch_t21_none"})
    )

    assert "make otel-setup" in call_error("get_trace", role="engineer", trace_id=TRACE)


# The aggregation tools on otel_demo ---------------------------------------------------------------------


def test_count_by_counts_otel_demo_without_a_time_slice(otel_demo: OtelDemo) -> None:
    result = call("count_by", role="team_lead", group_by="component", dataset="otel_demo", levels=["error"])

    counts = {row["component"]: row["count"] for row in row_dicts(result)}
    assert counts == {"cart": 80, "frontend": 41, "payment": 1}
    assert result["summary"]["total"] == 122


def test_trend_buckets_otel_demo_by_minute(otel_demo: OtelDemo) -> None:
    result = call(
        "trend",
        role="team_lead",
        dataset="otel_demo",
        bucket="minute",
        levels=["error"],
        components=["payment"],
    )

    assert result["summary"]["total"] == 1


def test_full_text_on_otel_demo_is_refused_with_the_reason(otel_demo: OtelDemo) -> None:
    text = call_error("count_by", role="engineer", group_by="component", dataset="otel_demo", query="visa")

    assert "not sorted by time" in text
