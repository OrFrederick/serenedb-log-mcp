"""The ledger replay engine (T13) without a database: a sample tool runs its SQL through the real
database layer, and a fake connection stands in for SereneDB (tests/support/replay_fake.py).
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any

import pytest
from replay_fake import (
    DATA,
    GOOD,
    SEEN,
    SQL,
    FakeConnection,
    fake_connect,
    fake_database,
    record,
    sample_server,
)

from serenedb_log_mcp import config, db, ledger
from serenedb_log_mcp.config import load_settings, set_settings
from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.export import Answer, Investigation
from serenedb_log_mcp.export.replay import compare_records, replay


@pytest.fixture(autouse=True)
def database() -> Iterator[None]:
    with fake_database():
        yield


def run(records: list[dict[str, Any]], investigation: Investigation | None = None) -> Any:
    return replay(records, investigation, server_factory=sample_server, connect=fake_connect)


def kinds(report: Any) -> list[str]:
    return [m.kind for m in report.mismatches]


def test_an_unchanged_ledger_replays_without_mismatch() -> None:
    records = record()

    report = run(records)

    assert report.ok, report.mismatches
    assert [r.evidence_id for r in report.records] == ["E1"]
    assert report.records[0].statements == 1
    assert report.records[0].rows_compared is True


def test_statements_run_with_the_parameters_as_stored() -> None:
    records = record()
    SEEN.clear()

    run(records)

    bound = [params for query, params in SEEN if query == SQL]
    assert bound == [["KERNDTLB", "2005-06-01T12:00:00"]]


def test_a_changed_database_is_a_mismatch() -> None:
    records = record()
    DATA["KERNDTLB"] = 152_735

    report = run(records)

    assert not report.ok
    assert "rows_changed" in kinds(report)
    assert "summary_changed" in kinds(report)


def test_a_tampered_total_is_a_mismatch() -> None:
    records = record()
    records[0]["total_matches"] = 2

    report = run(records)

    assert kinds(report) == ["total_changed"]
    assert "recorded 2, now 1" in report.mismatches[0].detail


def test_a_tampered_row_is_a_mismatch() -> None:
    records = record()
    records[0]["rows"][0][1] = 152_000

    assert kinds(run(records)) == ["rows_changed"]


def test_rows_of_a_relevance_sorted_result_are_not_compared() -> None:
    records = record(calls=[{"key": "KERNDTLB", "sort": "relevance"}])
    records[0]["rows"][0][1] = 152_000

    report = run(records)

    assert report.ok, report.mismatches
    assert report.records[0].rows_compared is False


def test_changed_sql_is_a_mismatch() -> None:
    records = record()
    records[0]["sql"][0] = SQL.replace("at >=", "at >")

    report = run(records)

    assert "sql_changed" in kinds(report)


def test_a_failing_statement_is_a_mismatch() -> None:
    records = record()
    records[0]["arguments"]["key"] = "boom"
    records[0]["params"][0][0] = "boom"

    report = run(records)

    assert kinds(report) == ["call_failed"]
    assert "incompatible" in report.mismatches[0].detail


def test_a_tool_this_server_does_not_offer_is_a_mismatch() -> None:
    records = record()
    records[0]["tool"] = "list_incidents_v9"

    assert kinds(run(records)) == ["not_replayable"]


def test_the_replay_restores_the_process_state(tmp_path: Any) -> None:
    settings = load_settings({}, ledger_path=tmp_path / "ledger.jsonl")
    set_settings(settings)
    ledger.reset_ledger()
    records = record()

    run(records)

    assert config.get_settings() == settings
    assert ledger.get_ledger().path == tmp_path / "ledger.jsonl"
    assert len(ledger.get_ledger().all()) == 1  # the replay appended nothing to LEDGER_PATH


def test_answers_are_checked_against_the_ledger() -> None:
    records = record()
    investigation = Investigation(title="t", answers=[Answer(role=Role.ENGINEER, question="q", answer=GOOD)])

    report = run(records, investigation)

    assert report.ok, report.mismatches
    assert report.answers[0].numbers == 1


def test_a_tampered_number_in_an_answer_is_a_mismatch() -> None:
    records = record()
    tampered = GOOD.replace("152,734", "152,743")
    investigation = Investigation(
        title="t", answers=[Answer(role=Role.ENGINEER, question="q", answer=tampered)]
    )

    report = run(records, investigation)

    assert not report.ok
    assert kinds(report) == ["number_not_in_evidence"]


def test_compare_records_tolerates_float_noise_but_not_type_changes() -> None:
    stored = record()[0]
    fresh = copy.deepcopy(stored)
    fresh["summary"]["share"] = stored["summary"]["share"] * (1 + 1e-12)

    assert compare_records(stored, fresh) == []

    fresh["summary"]["share"] = True
    assert [m.kind for m in compare_records(stored, fresh)] == ["summary_changed"]


def test_set_connector_switches_the_process_wide_layers() -> None:
    calls: list[str] = []

    def counting(dsn: str, *, client_binding: bool) -> FakeConnection:
        calls.append(dsn)
        return FakeConnection()

    db.set_connector(counting)
    db.fetch_all("SELECT 1")

    assert calls
    db.set_connector(None)
    assert db.get_database()._connect is db.open_connection
