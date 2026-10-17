"""`describe_corpus` end to end through MCP, on the synthetic corpus of tests/support/retrieval_corpus.py."""

from __future__ import annotations

from datetime import timedelta

import pytest
from aggregation_corpus import APPSEV, BGL_FIRST, BGL_LAST, NEWCOMP, OS_FIRST, STEADY, STORM
from aggregation_tools import call, independent, row_dicts
from retrieval_corpus import BGL_TOTAL, OS_TOTAL

pytestmark = pytest.mark.db


def test_both_datasets_with_rows_time_range_corpus_end_and_pattern_counts(corpus: str) -> None:
    result = call("describe_corpus", role="engineer")

    datasets = result["summary"]["datasets"]
    assert list(datasets) == ["bgl", "openstack"]
    bgl = datasets["bgl"]
    assert bgl["entries"] == BGL_TOTAL
    # UTC, the clock of every time input (#73): BGL's ts is logged in PDT here, UTC-7.
    assert bgl["first_entry"] == (BGL_FIRST + timedelta(hours=7)).isoformat() == "2005-06-01T16:00:00"
    assert bgl["last_entry"] == bgl["corpus_end"] == (BGL_LAST + timedelta(hours=7)).isoformat()
    assert datasets["openstack"]["first_entry"] == OS_FIRST.isoformat()
    assert bgl["patterns"] == 3
    assert bgl["entries_with_pattern"] == STEADY + STORM
    assert datasets["openstack"]["entries"] == OS_TOTAL
    assert datasets["openstack"]["patterns"] == 0
    assert result["summary"]["entries_total"] == BGL_TOTAL + OS_TOTAL
    assert result["data_scope"]["datasets"] == ["bgl", "openstack"]
    assert result["data_scope"]["time_zone"] == "UTC"


def test_components_come_with_counts_and_teams(corpus: str) -> None:
    result = call("describe_corpus", role="team_lead", dataset="bgl")

    assert result["columns"] == ["dataset", "kind", "value", "entries", "team"]
    components = {
        r["value"]: (r["entries"], r["team"]) for r in row_dicts(result) if r["kind"] == "component"
    }
    expected = dict(independent(corpus, "SELECT component, count(*) FROM {bgl} GROUP BY 1"))
    assert {name: n for name, (n, _) in components.items()} == expected
    assert components["KERNEL"] == (STEADY + STORM, "Kernel Team")
    assert components["NEWCOMP"] == (NEWCOMP, "(unmapped)")


def test_levels_and_alert_categories_are_listed_with_counts(corpus: str) -> None:
    rows = row_dicts(call("describe_corpus", role="engineer", dataset="bgl"))

    levels = {r["value"]: r["entries"] for r in rows if r["kind"] == "level"}
    alerts = {r["value"]: r["entries"] for r in rows if r["kind"] == "alert_category"}
    assert levels["FATAL"] == STORM + APPSEV
    assert alerts == {"KERNDTLB": STORM, "APPSEV": APPSEV}
    assert all(r["team"] is None for r in rows if r["kind"] != "component")


def test_one_dataset_can_be_described_alone(corpus: str) -> None:
    result = call("describe_corpus", role="engineer", dataset="openstack")

    assert list(result["summary"]["datasets"]) == ["openstack"]
    assert {r["dataset"] for r in row_dicts(result)} == {"openstack"}
    assert result["data_scope"]["datasets"] == ["openstack"]


def test_the_notes_say_log_entries_are_not_incidents_and_how_time_works(corpus: str) -> None:
    notes = " ".join(call("describe_corpus", role="manager")["notes"])

    assert "not Incidents" in notes
    assert "first_entry, last_entry and corpus_end are UTC" in notes
    assert "For a logged bgl ts, UTC = ts + 7 h" in notes
    assert "stub Ownership Table" in notes
    assert "No Pattern ids are assigned in openstack" in notes


def test_the_result_is_cached_after_the_first_call(corpus: str) -> None:
    first = call("describe_corpus", role="engineer")
    again = call("describe_corpus", role="engineer")

    assert again["evidence_id"] != first["evidence_id"]
    assert again["sql"] == first["sql"]
    assert again["query_ms"] == first["query_ms"]  # the statements did not run again
    assert again["rows"] == first["rows"]
    assert any("cache" in note for note in again["notes"])


def test_every_row_is_returned(corpus: str) -> None:
    result = call("describe_corpus", role="engineer")

    assert result["total_matches"] == result["returned"] == len(result["rows"])
    assert result["truncated"] is False
