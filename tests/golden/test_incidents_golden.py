"""Golden checks of `list_incidents` on the full Base Corpus (T27).

The tool's numbers are compared with the ticket's wording and with independent SQL on the canonical
tables, so the tool cannot pass by agreeing with its own derive step.
"""

from __future__ import annotations

import pytest
from aggregation_tools import call, row_dicts

from golden import Golden

pytestmark = pytest.mark.corpus


def test_the_largest_kerndtlb_incident_through_the_tool(golden_schema: str) -> None:
    """Acceptance criterion 4: "list_incidents(dataset='bgl', pattern_key='KERNDTLB', sort='entries')
    reports the largest Incident with 152,182 Log Entries on 1 node (2005-06-11), and the summary
    shows 152,734 Log Entries for the category"."""
    result = call("list_incidents", role="team_lead", dataset="bgl", pattern_key="KERNDTLB", sort="entries")

    largest = row_dicts(result)[0]
    assert (largest["entries"], largest["nodes"]) == (152_182, 1)
    assert largest["t0"].startswith("2005-06-11")
    assert largest["shape"] == "single-node storm"
    assert result["summary"]["entries_total"] == 152_734
    assert result["summary"]["computed"] == "precomputed table"
    Golden(golden_schema).value(
        result["summary"]["entries_total"], "SELECT count(*) FROM {bgl} WHERE alert_tag = 'KERNDTLB'"
    )


def test_the_kerndtlb_incident_count_matches_an_independent_chaining(golden_schema: str) -> None:
    result = call("list_incidents", role="manager", dataset="bgl", pattern_key="KERNDTLB")

    Golden(golden_schema).value(
        result["summary"]["incidents_total"],
        "SELECT count(*) FILTER (WHERE previous IS NULL OR epoch_utc - previous > 300) FROM ("
        "SELECT epoch_utc, lag(epoch_utc) OVER (ORDER BY epoch_utc, entry_id) AS previous "
        "FROM {bgl} WHERE alert_tag = 'KERNDTLB') chained",
    )


def test_openstack_request_failures_include_the_two_503_responses(golden_schema: str) -> None:
    result = call("list_incidents", role="team_lead", dataset="openstack")
    lifecycle = call(
        "list_incidents", role="team_lead", dataset="openstack", pattern_key="lifecycle-incomplete"
    )

    golden = Golden(golden_schema)
    failing = golden.scalar(
        "SELECT count(*) FROM {os} WHERE pattern_key IS NOT NULL "
        "AND (level IN ('ERROR', 'CRITICAL') OR http_status >= 500)"
    )
    assert result["summary"]["entries_total"] == failing + lifecycle["summary"]["entries_total"]
    # One instance in the Base Corpus starts its build and never finishes it.
    assert lifecycle["summary"]["incidents_total"] == 1
    assert golden.scalar("SELECT count(*) FROM {os} WHERE http_status >= 500") == 2
