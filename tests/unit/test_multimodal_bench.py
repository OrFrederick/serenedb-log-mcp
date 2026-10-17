"""bench/multimodal.py (T21): the selection SQL, the answer and the report, without a database."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import multimodal
from huge_scale import Plan

ROOT = Path(__file__).parents[2]
LOGS = "https://h/otel_demo/otel_logs.parquet"
TRACE = "f6206a8a53df0b7d2e47a9cc7ebf5b11"


def evidence(evidence_id: str, columns: list[str], rows: list[list[Any]], **summary: Any) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "columns": columns,
        "rows": rows,
        "summary": summary,
        "query_ms": 812.5,
        "total_matches": len(rows),
        "returned": len(rows),
        "truncated": False,
    }


def span(service: str, name: str, status: str, depth: int, text: str | None = None) -> list[Any]:
    return ["span", "2025-05-25T19:00:00.010000", service, name, "Server", status, 12.5, 2.5, depth,
            f"{depth:016x}", None, text, None, None, None, None, None]  # fmt: skip


def report() -> multimodal.Report:
    columns = list(multimodal.trace_tool.COLUMNS)
    trace_rows = [
        span("checkout", "oteldemo.CheckoutService/PlaceOrder", "Error", 1, "failed to charge card"),
        span("payment", "Error: Visa cache full: cannot add new item.", "Error", 2),
        [
            "log",
            "2025-05-25T19:00:00.038000",
            "payment",
            None,
            None,
            "error",
            None,
            None,
            None,
            "0000000000000002",
            None,
            "Visa cache full: cannot add new item.",
            None,
            None,
            None,
            None,
            None,
        ],
        [
            "metric",
            "2025-05-25T18:58:00",
            "payment",
            "http.client.duration",
            "histogram",
            "unusual_high",
            None,
            None,
            None,
            None,
            None,
            None,
            74.12,
            "ms",
            4.5,
            18.7,
            44.5,
        ],
        [
            "metric",
            "2025-05-25T18:58:00",
            "payment",
            "nodejs.event_loop.utilization",
            "gauge",
            "unusual_high",
            None,
            None,
            None,
            None,
            None,
            None,
            0.24,
            "1",
            0.015,
            0.025,
            0.21,
        ],
        [
            "metric",
            "2025-05-25T18:58:00",
            "payment",
            "visa_validation_cache.size",
            "gauge",
            "normal",
            None,
            None,
            None,
            None,
            None,
            None,
            100000.0,
            "entries",
            0.0,
            2067.0,
            100000.0,
        ],
        [
            "metric",
            "2025-05-25T18:58:00",
            "payment",
            "k8s.pod.memory.rss",
            "gauge",
            "unusual_high",
            None,
            None,
            None,
            None,
            None,
            None,
            106443025.0,
            "By",
            86892339.0,
            89525589.0,
            105244058.0,
        ],
    ]
    trace = evidence(
        "E4",
        columns,
        trace_rows,
        spans_total=2,
        error_spans=2,
        services=[
            {"service": "checkout", "spans": 1, "error_spans": 1},
            {"service": "payment", "spans": 1, "error_spans": 1},
        ],
        error_origin_spans=[
            {
                "span_id": "2",
                "service": "payment",
                "operation": "Error: Visa cache full: cannot add new item.",
                "depth": 2,
                "status_message": "",
            }
        ],
        slowest_self_span={
            "span_id": "1",
            "service": "checkout",
            "operation": "oteldemo.CheckoutService/PlaceOrder",
            "self_ms": 2.5,
            "duration_ms": 12.5,
        },
        trace_duration_ms=12.5,
        log_entries_total=1,
        error_log_entries=1,
        metrics_window={"from": "2025-05-25T18:58:00", "to": "2025-05-25T19:03:00"},
        metric_series_total=9,
        metric_series_returned=1,
        metric_series_unusual=1,
    )
    plan = Plan(total_ms=4390.0, row_groups=(1, 120), http_in=27_262_976)
    return multimodal.Report(
        files=[
            multimodal.FileInfo("logs", LOGS, 4_344_647_502, 91_559_064, 120),
            multimodal.FileInfo(
                "traces", "https://h/otel_demo/otel_traces.parquet", 12_934_790_411, 216_701_061, 354
            ),
            multimodal.FileInfo(
                "metrics_gauge", "https://h/otel_demo/otel_metrics_gauge.parquet", 435_453_837, 60_433_304, 81
            ),
        ],
        roll_up_rows=3_086_697,
        roll_up_series=1_121,
        count_by=evidence(
            "E1",
            ["dataset", "component", "count", "share_pct"],
            [["otel_demo", "frontend", 388_312, 66.68], ["otel_demo", "payment", 193_920, 33.3]],
            total=582_391,
        ),
        trend=evidence(
            "E2",
            ["bucket", "(all)"],
            [
                ["2025-05-25T16:00", 14],
                ["2025-05-25T17:00", 36_612],
                ["2025-05-25T19:00", 41_304],
                ["2025-05-25T22:00", 12],
            ],
            total=193_920,
            series=[{"peak_bucket": "2025-05-25T19:00", "peak_count": 41_304}],
        ),
        burst=evidence(
            "E3",
            ["dataset", "component", "count", "share_pct", "prev_period", "baseline_flag"],
            [["otel_demo", "payment", 193_526, 100.0, 58, "unusual_high"]],
            total=193_526,
        )
        | {"data_scope": {"time_from": "2025-05-25T17:00:00", "time_to": "2025-05-25T22:00:00"}},
        entry={
            "row": 5937348,
            "ts": "2025-05-25T19:00:00.038000",
            "service": "payment",
            "level": "error",
            "trace_id": TRACE,
            "span_id": "9c6485deae7e6edb",
            "message": "Visa cache full: cannot add new item.",
        },
        selection=[
            multimodal.Read(
                "selection, phase 1", "SELECT file_row_number FROM x", Plan(total_ms=250.0, http_in=0)
            ),
            multimodal.Read("selection, phase 2", "SELECT * FROM x WHERE file_row_number IN (1)", plan),
        ],
        trace=trace,
        trace_reads=[
            multimodal.Read(
                "get_trace: spans per service",
                "SELECT 1",
                Plan(total_ms=7000.0, row_groups=(23, 354), http_in=46_000_000),
            )
        ],
        environment={"SereneDB": "PostgreSQL 18.3 (SereneDB 26.10.1)"},
        ledger_file="docs/results/multimodal.ledger.jsonl",
    )


def test_phase_one_reads_only_narrow_columns_and_binds_the_hour() -> None:
    first, source = multimodal.selection_statements(LOGS, "payment", datetime(2025, 5, 25, 19))

    assert source == f"read_parquet('{LOGS}', file_row_number = true)"
    assert first.startswith("SELECT file_row_number FROM read_parquet(")
    assert "\"ServiceName\" = 'payment'" in first
    assert "CAST('2025-05-25 19:00:00+00' AS TIMESTAMPTZ)" in first
    assert "CAST('2025-05-25 20:00:00+00' AS TIMESTAMPTZ)" in first
    for wide in ('"Body"', '"TraceId"', '"Timestamp"'):
        assert wide not in first


def test_phase_two_reads_only_the_picked_rows() -> None:
    _, source = multimodal.selection_statements(LOGS, "payment", datetime(2025, 5, 25, 19))

    second = multimodal.entry_statement(source, [5937348, 5937349])

    assert "WHERE file_row_number IN (5937348, 5937349)" in second
    assert '"TraceId"' in second


def test_a_service_name_is_a_quoted_literal() -> None:
    first, _ = multimodal.selection_statements(LOGS, "it's", datetime(2025, 5, 25, 19))

    assert "'it''s'" in first


def test_the_answer_has_the_four_sections_and_cites_the_evidence() -> None:
    text = "\n".join(multimodal.answer(report()))

    for section in ("### Observed Facts", "### Hypotheses", "### Unknowns", "### Next Step (Engineer)"):
        assert section in text
    facts = text.split("### Hypotheses")[0]
    assert "388,312" in facts
    assert "[E1]" in facts
    assert "41,304" in facts
    assert "[E2]" in facts
    assert "193,526" in facts
    assert "58 in the 5 hours before" in facts
    assert "[E3]" in facts
    assert 'payment: span "Error: Visa cache full: cannot add new item."' in facts
    assert "[E4]" in facts
    assert "http.client.duration 74.12 ms against a median minute of 18.7 (unusual_high)" in facts
    assert "nodejs.event_loop.utilization 0.24 against a median minute of 0.025" in facts  # unit 1: none
    assert "k8s.pod.memory.rss 106,443,025 By against a median minute of 89,525,589" in facts
    # The furthest from its median among all metric rows, even one whose flag reads normal.
    assert (
        "visa_validation_cache.size was 100,000 entries in the window against a median minute of 2,067"
        in facts
    )
    hypotheses = text.split("### Hypotheses")[1].split("### Unknowns")[0]
    assert "http.client.duration, nodejs.event_loop.utilization and k8s.pod.memory.rss" in hypotheses
    assert "CPU" not in hypotheses
    assert "incident" not in text.lower()


def test_the_burst_is_the_contiguous_run_of_hours_with_at_least_a_tenth_of_the_peak() -> None:
    trend = report().trend

    assert multimodal.burst_window(trend) == (datetime(2025, 5, 25, 17), datetime(2025, 5, 25, 20))


def test_the_report_states_reads_data_and_where_the_evidence_is() -> None:
    text = multimodal.render(report())

    assert text.startswith("# Multimodal correlation")
    assert "docs/results/multimodal.ledger.jsonl" in text
    assert "23 of 354 row groups" in text
    assert "| otel_logs.parquet | Log Entries | 91,559,064 | 120 |" in text
    assert "search_logs (T5)" in text
    assert "never money" in text


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_bench_multimodal_runs_in_the_huge_schema() -> None:
    out = subprocess.run(
        ["make", "-n", "bench-multimodal"], cwd=ROOT, capture_output=True, text=True, timeout=30, check=True
    ).stdout

    assert "SLM_SCHEMA=huge" in out
    assert "bench/multimodal.py" in out


def test_phase_one_takes_the_last_error_log_entries_of_the_hour() -> None:
    first, _ = multimodal.selection_statements(LOGS, "payment", datetime(2025, 5, 25, 19))

    assert first.endswith('ORDER BY "TimestampTime" DESC, file_row_number DESC LIMIT 4')


def test_a_measured_run_round_trips_through_json_and_renders_the_same() -> None:
    original = report()

    again = multimodal.from_json(multimodal.to_json(original))

    assert again == original
    assert multimodal.render(again) == multimodal.render(original)


def test_the_request_is_picked_in_the_last_hour_of_the_burst() -> None:
    assert multimodal.selection_hour(report().burst) == datetime(2025, 5, 25, 21)
