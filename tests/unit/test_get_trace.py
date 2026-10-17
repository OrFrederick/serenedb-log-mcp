"""`get_trace` (T21): one OTel trace's spans, its Log Entries and its services' metrics, as Evidence.

The database is replaced by a fake that answers each of the tool's statements;
tests/db/otel/test_get_trace_db.py runs the same tool against real views over synthetic Parquet files.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult

from serenedb_log_mcp.config import Role, load_settings, set_settings
from serenedb_log_mcp.db import QueryFailed, QueryResult, QueryTimeout
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.toolkit import ALWAYS_LOAD_META
from serenedb_log_mcp.tools import get_trace as module

TRACE = "b60c4584ee2689aaa9751261d1b9d800"
T0 = datetime(2025, 5, 25, 20, 1, 41, 636879)

SERVICES = [
    # service, spans, error_spans, first_start, last_end
    ("load-generator", 1, 1, T0, datetime(2025, 5, 25, 20, 1, 41, 754944)),
    ("checkout", 2, 2, datetime(2025, 5, 25, 20, 1, 41, 688324), datetime(2025, 5, 25, 20, 1, 41, 727381)),
    ("payment", 2, 1, datetime(2025, 5, 25, 20, 1, 41, 712000), datetime(2025, 5, 25, 20, 1, 41, 725851)),
]
SPANS = [
    # ts, service, operation, kind, status, duration_ms, self_ms, depth, span_id, parent_span_id,
    # status_message, error_origin, error_origins, slowest
    (
        datetime(2025, 5, 25, 20, 1, 41, 725000),
        "payment",
        "Error: Visa cache full: cannot add new item.",
        "Internal",
        "Error",
        0.0,
        0.0,
        4,
        "881733e3c479a8aa",
        "f3b1c869cdc6c529",
        "",
        True,
        1,
        False,
    ),
    (
        T0,
        "load-generator",
        "POST",
        "Client",
        "Error",
        118.065,
        79.008,
        0,
        "da8d6cc2c79cadc8",
        "",
        "",
        False,
        1,
        True,
    ),
    (
        datetime(2025, 5, 25, 20, 1, 41, 688324),
        "checkout",
        "oteldemo.CheckoutService/PlaceOrder",
        "Server",
        "Error",
        39.057,
        23.071,
        1,
        "7042bdc760840967",
        "da8d6cc2c79cadc8",
        "failed to charge card: Visa cache full: cannot add new item.",
        False,
        1,
        False,
    ),
    (
        datetime(2025, 5, 25, 20, 1, 41, 711296),
        "checkout",
        "oteldemo.PaymentService/Charge",
        "Client",
        "Error",
        15.986,
        2.135,
        2,
        "52b0dd8a06d0226c",
        "7042bdc760840967",
        "Visa cache full: cannot add new item.",
        False,
        1,
        False,
    ),
    (
        datetime(2025, 5, 25, 20, 1, 41, 712000),
        "payment",
        "grpc.oteldemo.PaymentService/Charge",
        "Server",
        "Unset",
        13.851,
        13.851,
        3,
        "f3b1c869cdc6c529",
        "52b0dd8a06d0226c",
        "",
        False,
        1,
        False,
    ),
]
LEVELS = [
    # component, level, n, first, last
    (
        "payment",
        "info",
        1,
        datetime(2025, 5, 25, 20, 1, 41, 712000),
        datetime(2025, 5, 25, 20, 1, 41, 712000),
    ),
    (
        "payment",
        "error",
        1,
        datetime(2025, 5, 25, 20, 1, 41, 725000),
        datetime(2025, 5, 25, 20, 1, 41, 725000),
    ),
]
LOGS = [
    # ts_exact, component, level, span_id, message
    (
        datetime(2025, 5, 25, 20, 1, 41, 725000),
        "payment",
        "error",
        "f3b1c869cdc6c529",
        "Visa cache full: cannot add new item.",
    ),
    (
        datetime(2025, 5, 25, 20, 1, 41, 712000),
        "payment",
        "info",
        "f3b1c869cdc6c529",
        "Charge request received.",
    ),
]
METRICS = [
    # service, metric, unit, kind, value, p05, median, p95, flag, minutes, series_total, unusual_total
    (
        "payment",
        "http.client.duration",
        "ms",
        "histogram",
        74.1211,
        4.5359,
        18.7086,
        44.5239,
        "unusual_high",
        5,
        9,
        2,
    ),
    (
        "payment",
        "container.cpu.utilization",
        "1",
        "gauge",
        0.1379,
        0.0208,
        0.0268,
        0.1215,
        "unusual_high",
        5,
        9,
        2,
    ),
    ("checkout", "rpc.server.duration", "ms", "histogram", 30.1, 12.0, 25.0, 41.0, "normal", 5, 9, 2),
]


class FakeDatabase:
    """Answers the get_trace statements by what they read; records (sql, params, timeout_s)."""

    def __init__(self, **answers: Sequence[tuple[Any, ...]] | Exception) -> None:
        self.answers: dict[str, Sequence[tuple[Any, ...]] | Exception] = {
            "services": SERVICES,
            "spans": SPANS,
            "levels": LEVELS,
            "logs": LOGS,
            "metrics": METRICS,
            **answers,
        }
        self.calls: list[tuple[str, list[Any], float | None]] = []

    @staticmethod
    def kind(sql: str) -> str:
        if "otel_metric_minute" in sql:
            return "metrics"
        if "otel_span" in sql:
            return "spans" if "RECURSIVE" in sql else "services"
        return "levels" if "GROUP BY component, level" in sql else "logs"

    def __call__(
        self,
        sql: str,
        params: Sequence[Any] = (),
        *,
        role: Role | None = None,
        timeout_s: float | None = None,
    ) -> QueryResult:
        self.calls.append((sql, list(params), timeout_s))
        answer = self.answers[self.kind(sql)]
        if isinstance(answer, Exception):
            raise answer
        rows = [tuple(row) for row in answer]
        return QueryResult(len(rows), rows, False, 12.5, [sql], [list(params)])

    def kinds(self) -> list[str]:
        return [self.kind(sql) for sql, _, _ in self.calls]


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> FakeDatabase:
    fake = FakeDatabase()
    monkeypatch.setattr(module, "fetch_all", fake)
    return fake


def call(**arguments: Any) -> CallToolResult:
    result = anyio.run(lambda: create_server().call_tool("get_trace", {"role": "engineer", **arguments}))
    assert isinstance(result, CallToolResult)
    return result


def evidence(**arguments: Any) -> dict[str, Any]:
    result = call(**arguments)
    assert not result.is_error, result.content[0].text if result.content else result
    assert result.structured_content is not None
    return result.structured_content


def error(**arguments: Any) -> str:
    with pytest.raises(ToolError) as caught:
        call(**arguments)
    return str(caught.value)


def by_signal(found: dict[str, Any], signal: str) -> list[dict[str, Any]]:
    rows = [dict(zip(found["columns"], row, strict=True)) for row in found["rows"]]
    return [row for row in rows if row["signal"] == signal]


# Registration and description -----------------------------------------------------------------------


def test_get_trace_is_a_read_only_tool_that_always_loads() -> None:
    tool = next(t for t in anyio.run(create_server().list_tools) if t.name == "get_trace")

    assert tool.meta == ALWAYS_LOAD_META
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert set(tool.input_schema["required"]) == {"role", "trace_id"}


def test_the_description_says_when_to_use_get_trace_instead_of_get_context() -> None:
    tool = next(t for t in anyio.run(create_server().list_tools) if t.name == "get_trace")
    description = " ".join((tool.description or "").split())

    assert "instead of get_context" in description
    assert "trace_id" in description
    assert "Inputs:" in description
    assert "Outputs:" in description
    assert "not a frequency" in description
    for column in module.COLUMNS:
        assert column in description


# Inputs ---------------------------------------------------------------------------------------------


def test_a_trace_id_in_capitals_or_with_spaces_is_normalised(database: FakeDatabase) -> None:
    found = evidence(trace_id=f"  {TRACE.upper()} ")

    assert found["data_scope"]["filters"]["trace_id"] == TRACE
    assert all(params[0] == TRACE for sql, params, _ in database.calls if database.kind(sql) != "metrics")


@pytest.mark.parametrize(
    "bad", ["b60c4584", "zz0c4584ee2689aaa9751261d1b9d800", "b60c4584-ee26-89aa-a975-1261d1b9d800"]
)
def test_a_malformed_trace_id_is_refused_before_any_query(database: FakeDatabase, bad: str) -> None:
    text = error(trace_id=bad)

    assert "32 hexadecimal characters" in text
    assert database.calls == []


# The Evidence -----------------------------------------------------------------------------------------


def test_spans_log_entries_and_metrics_come_back_as_one_evidence(database: FakeDatabase) -> None:
    found = evidence(trace_id=TRACE)

    assert found["columns"] == list(module.COLUMNS)
    spans, logs, metrics = by_signal(found, "span"), by_signal(found, "log"), by_signal(found, "metric")
    assert [row["span_id"] for row in spans] == [
        "da8d6cc2c79cadc8",
        "7042bdc760840967",
        "52b0dd8a06d0226c",
        "f3b1c869cdc6c529",
        "881733e3c479a8aa",
    ]  # by start time, whatever order the statement kept them in
    assert [row["status"] for row in logs] == ["info", "error"]  # by time
    assert logs[1]["span_id"] == "f3b1c869cdc6c529"
    assert logs[1]["text"] == "Visa cache full: cannot add new item."
    assert [row["name"] for row in metrics] == [
        "http.client.duration",
        "container.cpu.utilization",
        "rpc.server.duration",
    ]
    assert metrics[0] == {
        **{column: None for column in module.COLUMNS},
        "signal": "metric",
        "ts": "2025-05-25T19:59:00",
        "service": "payment",
        "name": "http.client.duration",
        "kind": "histogram",
        "status": "unusual_high",
        "value": 74.1211,
        "unit": "ms",
        "baseline_p05": 4.5359,
        "baseline_median": 18.7086,
        "baseline_p95": 44.5239,
    }
    assert found["total_matches"] == 5 + 2 + 9
    assert found["returned"] == 5 + 2 + 3
    assert found["truncated"] is True


def test_the_summary_names_the_failing_and_the_slowest_span(database: FakeDatabase) -> None:
    summary = evidence(trace_id=TRACE)["summary"]

    assert summary["trace_id"] == TRACE
    assert summary["trace_start"] == "2025-05-25T20:01:41.636879"
    assert summary["trace_end"] == "2025-05-25T20:01:41.754944"
    assert summary["trace_duration_ms"] == 118.065
    assert summary["spans_total"] == 5
    assert summary["error_spans"] == 4
    assert summary["services"] == [
        {"service": "load-generator", "spans": 1, "error_spans": 1},
        {"service": "checkout", "spans": 2, "error_spans": 2},
        {"service": "payment", "spans": 2, "error_spans": 1},
    ]
    assert summary["error_origin_spans"] == [
        {
            "span_id": "881733e3c479a8aa",
            "service": "payment",
            "operation": "Error: Visa cache full: cannot add new item.",
            "depth": 4,
            "status_message": "",
        }
    ]
    assert summary["slowest_self_span"]["span_id"] == "da8d6cc2c79cadc8"
    assert summary["log_entries_total"] == 2
    assert summary["log_levels"] == {"error": 1, "info": 1}
    assert summary["error_log_entries"] == 1
    assert summary["metrics_window"] == {"from": "2025-05-25T19:59:00", "to": "2025-05-25T20:04:00"}
    assert summary["metric_series_total"] == 9
    assert summary["metric_series_unusual"] == 2


def test_the_data_scope_names_the_trace_and_the_metric_window(database: FakeDatabase) -> None:
    scope = evidence(trace_id=TRACE, pad_minutes=0)["data_scope"]

    assert scope["datasets"] == ["otel_demo"]
    assert scope["filters"]["trace_id"] == TRACE
    assert scope["filters"]["metrics_window"] == {"from": "2025-05-25T20:01:00", "to": "2025-05-25T20:02:00"}
    assert scope["filters"]["metrics_services"] == ["load-generator", "checkout", "payment"]


def test_every_statement_binds_its_values_and_the_metric_one_reads_the_roll_up(
    database: FakeDatabase,
) -> None:
    found = evidence(trace_id=TRACE, span_limit=7, log_limit=3, metric_limit=4)

    assert database.kinds() == ["services", "spans", "levels", "logs", "metrics"]
    assert all(TRACE not in sql for sql, _, _ in database.calls)
    assert [params for _, params, _ in database.calls] == [
        [TRACE],
        [TRACE, 7],
        [TRACE],
        [TRACE, 3],
        [
            ["load-generator", "checkout", "payment"],
            datetime(2025, 5, 25, 19, 59),
            datetime(2025, 5, 25, 20, 4),
            4,
        ],
    ]
    assert len(found["sql"]) == 5
    assert len(found["params"]) == 5


def test_remote_statements_get_the_trace_timeout_and_the_roll_up_the_default(
    database: FakeDatabase,
) -> None:
    set_settings(load_settings({"SLM_TRACE_TIMEOUT_S": "90"}))

    evidence(trace_id=TRACE)

    assert [timeout for _, _, timeout in database.calls] == [90.0, 90.0, 90.0, 90.0, None]


def test_nothing_truncated_means_no_truncation_note(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics = [(*row[:10], 3, 2) for row in METRICS]
    monkeypatch.setattr(module, "fetch_all", FakeDatabase(metrics=metrics))

    found = evidence(trace_id=TRACE)

    assert found["truncated"] is False
    assert not any("Showing" in note for note in found["notes"])


def test_a_truncated_signal_says_which_one_and_what_was_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    services = [(*SERVICES[0][:1], 120, 1, *SERVICES[0][3:])]
    monkeypatch.setattr(module, "fetch_all", FakeDatabase(services=services))

    found = evidence(trace_id=TRACE)

    note = next(note for note in found["notes"] if note.startswith("Showing"))
    assert "5 of 120 spans" in note
    assert "Error spans first" in note
    assert "summary" in note


# Edges -------------------------------------------------------------------------------------------------


def test_an_unknown_trace_is_evidence_of_nothing_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDatabase(services=[], levels=[])
    monkeypatch.setattr(module, "fetch_all", fake)

    found = evidence(trace_id=TRACE)

    assert found["total_matches"] == 0
    assert found["rows"] == []
    assert fake.kinds() == ["services", "levels"]  # no rows to fetch, no window for metrics
    assert any("No span and no Log Entry" in note for note in found["notes"])


def test_log_entries_without_spans_still_get_metrics_around_them(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDatabase(services=[])
    monkeypatch.setattr(module, "fetch_all", fake)

    found = evidence(trace_id=TRACE, pad_minutes=1)

    assert fake.kinds() == ["services", "levels", "logs", "metrics"]
    assert fake.calls[-1][1][:3] == [["payment"], datetime(2025, 5, 25, 20, 0), datetime(2025, 5, 25, 20, 3)]
    assert found["summary"]["spans_total"] == 0
    assert any("no span" in note.lower() for note in found["notes"])


def test_a_timeout_explains_the_cold_remote_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "fetch_all", FakeDatabase(services=QueryTimeout("ran longer than 120 s")))

    text = error(trace_id=TRACE)

    assert "SLM_TRACE_TIMEOUT_S" in text
    assert "bloom filter" in text
    assert "make otel-setup" in text


def test_missing_views_say_how_to_set_them_up(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = QueryFailed('relation "otel_span" does not exist', "42P01")
    monkeypatch.setattr(module, "fetch_all", FakeDatabase(services=missing))

    assert "make otel-setup" in error(trace_id=TRACE)


def test_a_missing_metric_roll_up_leaves_the_spans_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = QueryFailed('relation "otel_metric_minute" does not exist', "42P01")
    monkeypatch.setattr(module, "fetch_all", FakeDatabase(metrics=missing))

    found = evidence(trace_id=TRACE)

    assert by_signal(found, "metric") == []
    assert len(by_signal(found, "span")) == 5
    assert any("make otel-setup" in note for note in found["notes"])


def test_the_metric_window_covers_whole_minutes_around_the_trace() -> None:
    start, end = datetime(2025, 5, 25, 20, 1, 41, 636879), datetime(2025, 5, 25, 20, 3, 0)

    assert module.metrics_window(start, end, 0) == (
        datetime(2025, 5, 25, 20, 1),
        datetime(2025, 5, 25, 20, 4),
    )
    assert module.metrics_window(start, end, 2) == (
        datetime(2025, 5, 25, 19, 59),
        datetime(2025, 5, 25, 20, 6),
    )
