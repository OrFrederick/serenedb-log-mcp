"""`list_incidents(similar_to=...)` end to end through MCP, on tests/support/precursor_corpus.py.

The look-alikes of the KERNPAN Incident REF are known by construction: E1 has the same Pattern set,
Component and Shape; E4 the same Pattern set and Component but another Shape; E2 shares two of three
Patterns; LATER starts after REF and is never listed.
"""

from __future__ import annotations

from typing import Any

import pytest
from aggregation_tools import call, call_error, row_dicts
from precursor_corpus import LOOKALIKE, P_IDS, REF_ID, T_A, T_B, T_FAIL

pytestmark = pytest.mark.db


def _similar(**arguments: Any) -> dict[str, Any]:
    return call(
        "list_incidents",
        role="engineer",
        dataset="bgl",
        pattern_key=LOOKALIKE,
        similar_to=REF_ID,
        **arguments,
    )


def test_the_same_pattern_set_component_and_shape_ranks_first(lab: str) -> None:
    rows = row_dicts(_similar())

    assert [(row["pattern_key"], row["incident_id"]) for row in rows[:3]] == [
        (LOOKALIKE, P_IDS["E1"]),
        (LOOKALIKE, P_IDS["E4"]),
        (LOOKALIKE, P_IDS["E2"]),
    ]
    assert [row["jaccard"] for row in rows[:3]] == [1, 1, pytest.approx(0.6667, abs=1e-4)]
    assert [row["same_component"] for row in rows[:3]] == [True, True, True]
    assert rows[0]["shape"] == "single-node isolated"
    assert rows[1]["shape"] == "one midplane"


def test_only_earlier_incidents_are_ranked(lab: str) -> None:
    result = _similar()

    ref_t0 = result["summary"]["reference"]["t0"]
    assert all(row["t0"] < ref_t0 for row in row_dicts(result))
    assert (LOOKALIKE, P_IDS["LATER"]) not in [
        (r["pattern_key"], r["incident_id"]) for r in row_dicts(result)
    ]


def test_incidents_of_other_patterns_with_a_smaller_overlap_rank_below(lab: str) -> None:
    rows = row_dicts(_similar())

    others = [row for row in rows if row["pattern_key"] in (T_A, T_B)]
    assert others
    assert all(row["jaccard"] < 0.6667 for row in others)
    assert all(row["same_component"] is False for row in others)


def test_the_summary_states_the_reference_and_its_signature(lab: str) -> None:
    result = _similar()

    summary = result["summary"]
    assert summary["similar_to"] == REF_ID
    assert summary["reference"]["pattern_key"] == LOOKALIKE
    assert sorted(summary["reference_patterns"]) == sorted([LOOKALIKE, T_A, T_B])
    assert summary["computed"] == "precomputed table"
    text = " ".join(result["notes"])
    assert "Jaccard" in text
    assert "BM25" in text  # the note says it is not a text-relevance ranking


def test_a_limit_lists_the_most_similar_first(lab: str) -> None:
    result = _similar(limit=1)

    assert [(r["pattern_key"], r["incident_id"]) for r in row_dicts(result)] == [(LOOKALIKE, P_IDS["E1"])]
    assert result["truncated"] is True


def test_similar_to_needs_a_pattern_key(lab: str) -> None:
    text = call_error("list_incidents", role="engineer", dataset="bgl", similar_to=REF_ID)

    assert "pattern_key" in text


def test_an_unknown_reference_incident_is_an_actionable_error(lab: str) -> None:
    text = call_error("list_incidents", role="engineer", dataset="bgl", pattern_key=LOOKALIKE, similar_to=99)

    assert "list_incidents" in text


def test_openstack_incidents_have_look_alikes_too(lab: str) -> None:
    result = call("list_incidents", role="engineer", dataset="openstack", pattern_key=T_FAIL, similar_to=3)

    rows = row_dicts(result)
    assert [(row["pattern_key"], row["incident_id"]) for row in rows[:2]] == [(T_FAIL, 2), (T_FAIL, 1)]
