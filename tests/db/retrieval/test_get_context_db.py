"""`get_context` end to end through MCP, on the synthetic corpus of tests/support/retrieval_corpus.py."""

from __future__ import annotations

from datetime import timedelta

import pytest
from aggregation_corpus import NODE_B, OS_FIRST, STORM, STORM_START, U1
from aggregation_tools import call, call_error, independent, row_dicts
from retrieval_corpus import ERROR_TS, REQ, REQ_ENTRIES, TERMINATING_TS, U1_INSTANCE_SCOPE

pytestmark = pytest.mark.db


def storm_ids(schema: str) -> list[int]:
    return [
        int(row[0])
        for row in independent(
            schema, "SELECT entry_id FROM {bgl} WHERE node = %s ORDER BY epoch_utc, entry_id", [NODE_B]
        )
    ]


def os_id(schema: str, where: str, params: list[object]) -> int:
    return int(independent(schema, "SELECT entry_id FROM {os} WHERE " + where, params)[0][0])


def test_the_node_scope_returns_the_nearest_entries_on_the_anchor_node_in_time_order(corpus: str) -> None:
    anchor = storm_ids(corpus)[50]

    result = call("get_context", role="engineer", dataset="bgl", entry_id=anchor)

    rows = row_dicts(result)
    assert result["columns"][:3] == ["entry_id", "ts", "offset_s"]
    assert result["columns"][-2:] == ["message", "anchor"]
    assert len(rows) == result["returned"] == 21
    assert result["total_matches"] == STORM
    assert result["truncated"] is True
    assert [r["anchor"] for r in rows].index(True) == 10
    assert sum(r["anchor"] for r in rows) == 1
    assert rows[10]["entry_id"] == anchor
    assert [r["offset_s"] for r in rows] == [30.0 * i for i in range(-10, 11)]
    assert {r["node"] for r in rows} == {NODE_B}
    summary = result["summary"]
    assert (summary["scope"], summary["scope_value"]) == ("node", NODE_B)
    assert (summary["before_returned"], summary["after_returned"]) == (10, 10)
    assert result["data_scope"]["filters"]["anchor_entry_id"] == anchor


def test_the_first_entry_of_a_node_has_nothing_before_it_and_says_so(corpus: str) -> None:
    result = call("get_context", role="engineer", dataset="bgl", entry_id=storm_ids(corpus)[0], after=3)

    rows = row_dicts(result)
    assert [r["offset_s"] for r in rows] == [0.0, 30.0, 60.0, 90.0]
    assert rows[0]["anchor"] is True
    assert any("Nothing earlier" in note for note in result["notes"])


def test_the_window_scope_covers_every_source_within_the_window(corpus: str) -> None:
    anchor = storm_ids(corpus)[50]
    anchor_ts = STORM_START + timedelta(seconds=30 * 50)

    result = call("get_context", role="engineer", dataset="bgl", entry_id=anchor, scope="window", window_s=60)

    assert result["total_matches"] == result["returned"] == 4
    assert result["truncated"] is False
    assert result["data_scope"]["time_from"] == (anchor_ts - timedelta(seconds=60)).isoformat()
    assert result["data_scope"]["time_to"] == (anchor_ts + timedelta(seconds=60)).isoformat()
    assert result["summary"]["scope"] == "window"


def test_window_s_also_narrows_a_source_scope(corpus: str) -> None:
    result = call("get_context", role="engineer", dataset="bgl", entry_id=storm_ids(corpus)[50], window_s=60)

    assert result["summary"]["scope"] == "node"
    assert result["total_matches"] == 4


def test_an_openstack_anchor_with_a_request_follows_the_request(corpus: str) -> None:
    anchor = os_id(corpus, "ts = %s AND level = 'ERROR'", [ERROR_TS[0]])

    result = call("get_context", role="engineer", dataset="openstack", entry_id=anchor)

    rows = row_dicts(result)
    assert result["summary"]["scope"] == "request"
    assert result["summary"]["scope_value"] == REQ
    assert result["total_matches"] == REQ_ENTRIES
    assert [r["ts"] for r in rows] == [t.isoformat() for t in (ERROR_TS[0], TERMINATING_TS, ERROR_TS[1])]
    assert [r["anchor"] for r in rows] == [True, False, False]
    assert result["columns"][3:7] == ["component", "level", "request_id", "instance_ref"]


def test_the_instance_scope_matches_the_instance_id_or_the_uuid_in_the_message(corpus: str) -> None:
    anchor = os_id(corpus, "ts = %s AND level = 'ERROR'", [ERROR_TS[0]])

    result = call("get_context", role="engineer", dataset="openstack", entry_id=anchor, scope="instance")

    rows = row_dicts(result)
    assert result["summary"]["scope_value"] == U1
    assert result["total_matches"] == U1_INSTANCE_SCOPE
    assert result["returned"] == 13  # 10 before, the anchor, and the 2 entries after it
    assert "Terminating instance" in [r["message"] for r in rows]  # found by instance_id only


def test_a_scope_the_anchor_lacks_is_an_error_with_an_alternative(corpus: str) -> None:
    anchor = os_id(corpus, "request_id = %s", ["req-22222222-aaaa-bbbb-cccc-000000000002"])

    text = call_error("get_context", role="engineer", dataset="openstack", entry_id=anchor, scope="instance")

    assert "no instance id" in text
    assert "'window'" in text


def test_an_anchor_without_request_or_instance_falls_back_to_a_window(corpus: str) -> None:
    anchor = os_id(corpus, "ts = %s", [OS_FIRST + timedelta(minutes=10)])

    result = call("get_context", role="engineer", dataset="openstack", entry_id=anchor)

    assert result["summary"]["scope"] == "window"
    assert result["summary"]["window_s"] == 60
    assert result["total_matches"] == 1


def test_an_unknown_entry_id_is_an_error_that_says_where_ids_come_from(corpus: str) -> None:
    text = call_error("get_context", role="engineer", dataset="bgl", entry_id=999_999)

    assert "entry_id=999999" in text
    assert "search_logs" in text


def test_a_scope_of_the_other_dataset_is_an_error(corpus: str) -> None:
    text = call_error(
        "get_context", role="engineer", dataset="bgl", entry_id=storm_ids(corpus)[0], scope="request"
    )

    assert "OpenStack" in text


def test_the_evidence_holds_the_lookup_count_and_rows_on_the_base_table(corpus: str) -> None:
    result = call("get_context", role="engineer", dataset="bgl", entry_id=storm_ids(corpus)[50])

    assert len(result["sql"]) == len(result["params"]) == 3
    for statement in result["sql"]:
        assert '"bgl_entry"' in statement
        assert "_idx" not in statement
        assert "@@" not in statement


def test_before_and_after_are_capped_at_50(corpus: str) -> None:
    assert "50" in call_error("get_context", role="engineer", dataset="bgl", entry_id=1, before=51)
