"""The Role shaping on the real retrieval and aggregation tools (T7), on the synthetic corpus.

The same search_logs call returns every requested row to the Engineer, three samples to the Team
Lead and none to the Manager, with one exact total and the same facets; get_context and get_evidence
follow the same rule, and aggregation rows reach the Manager whole.
"""

from __future__ import annotations

import pytest
from aggregation_corpus import NODE_B, STORM
from aggregation_tools import call, row_dicts

from serenedb_log_mcp import toolkit
from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.roles import NOT_MEASURED, answer_frame

pytestmark = pytest.mark.db


@pytest.fixture
def shaped(corpus: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The synthetic corpus with the product's Role shaping instead of the identity hook."""
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.DEFAULT_ROLE_HOOK)
    return corpus


def test_search_logs_differs_by_role_in_rows_but_not_in_totals(shaped: str) -> None:
    results = {role: call("search_logs", role=role.value, dataset="bgl", query="TLB error") for role in Role}

    assert [results[role]["returned"] for role in Role] == [20, 3, 0]
    assert {results[role]["total_matches"] for role in Role} == {STORM}
    assert results[Role.ENGINEER]["summary"] == results[Role.MANAGER]["summary"]
    assert results[Role.MANAGER]["summary"]["top_components"] == {"KERNEL": STORM}
    assert results[Role.MANAGER]["rows"] == []
    assert results[Role.MANAGER]["truncated"] is True
    assert results[Role.MANAGER]["not_measured"] == list(NOT_MEASURED)
    assert "withheld" in results[Role.MANAGER]["notes"][0]
    assert [r["entry_id"] for r in row_dicts(results[Role.TEAM_LEAD])] == [
        r["entry_id"] for r in row_dicts(results[Role.ENGINEER])[:3]
    ]
    for role in Role:
        assert results[role]["answer_frame"] == answer_frame(role)


def test_get_context_keeps_the_anchor_for_the_team_lead_and_nothing_for_the_manager(shaped: str) -> None:
    # The 11th storm entry: its node logged at least three Log Entries before it and after it.
    found = call("search_logs", role="engineer", dataset="bgl", query="TLB error", limit=20)
    anchor = row_dicts(found)[10]["entry_id"]

    engineer = call("get_context", role="engineer", dataset="bgl", entry_id=anchor, before=3, after=3)
    team_lead = call("get_context", role="team_lead", dataset="bgl", entry_id=anchor, before=3, after=3)
    manager = call("get_context", role="manager", dataset="bgl", entry_id=anchor, before=3, after=3)

    assert engineer["returned"] == 7
    assert [r["anchor"] for r in row_dicts(team_lead)] == [False, True, False]
    assert manager["rows"] == []
    assert manager["summary"]["scope_value"] == NODE_B
    assert engineer["total_matches"] == team_lead["total_matches"] == manager["total_matches"]


def test_get_evidence_withholds_the_engineers_rows_from_the_manager(shaped: str) -> None:
    produced = call("search_logs", role="engineer", dataset="bgl", query="TLB error")

    again = call("get_evidence", role="manager", evidence_id=produced["evidence_id"])

    assert produced["returned"] == 20
    assert again["rows"] == []
    assert again["total_matches"] == STORM
    assert again["sql"] == produced["sql"]


def test_count_by_reaches_the_manager_whole(shaped: str) -> None:
    engineer = call("count_by", role="engineer", group_by="alert_category", dataset="bgl")
    manager = call("count_by", role="manager", group_by="alert_category", dataset="bgl")

    assert manager["rows"] == engineer["rows"]
    assert manager["rows"]
    assert manager["not_measured"] == list(NOT_MEASURED)
