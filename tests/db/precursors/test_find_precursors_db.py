"""`find_precursors` end to end through MCP, on the lab corpus of tests/support/precursor_corpus.py.

Every precursor, delay and baseline here is known by construction; the baseline is also recomputed
by brute force in Python (`precursor_corpus.expected_baseline`), so the SQL cannot pass by agreeing
with itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from aggregation_tools import call, call_error, row_dicts
from precursor_corpus import (
    BEAT_NODE,
    OTHER_NODE_TARGETS,
    SAME_NODE_DELAYS,
    T_BEAT,
    T_FAIL,
    T_SPAWN,
    T_WARN,
    TARGET,
    TARGET_NODES,
    expected_baseline,
)

pytestmark = pytest.mark.db


def _by_key(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["precursor"]: row for row in row_dicts(result)}


# ----------------------------------------------------------------- same source (the default)


def test_the_same_node_precursor_passes_with_its_support_rate_and_delays(lab: str) -> None:
    result = call("find_precursors", role="engineer", dataset="bgl", target_pattern=TARGET)

    rows = _by_key(result)
    assert list(rows) == ["KERNTERM"]
    term = rows["KERNTERM"]
    assert term["support"] == len(SAME_NODE_DELAYS)
    assert term["units_preceded"] == len(SAME_NODE_DELAYS)
    assert term["rate"] == pytest.approx(len(SAME_NODE_DELAYS) / len(TARGET_NODES), abs=1e-4)
    assert (term["delay_median_s"], term["delay_min_s"], term["delay_max_s"]) == (30, 20, 50)
    summary = result["summary"]
    assert summary["incidents_total"] == len(TARGET_NODES)
    assert summary["units_total"] == len(TARGET_NODES)
    assert summary["window_s"] == 60
    assert summary["min_lag_s"] == 1
    assert summary["same_source"] is True
    assert result["total_matches"] == 1


def test_the_baseline_comes_from_the_probe_grid_per_source(lab: str) -> None:
    result = call("find_precursors", role="engineer", dataset="bgl", target_pattern=TARGET)

    term = _by_key(result)["KERNTERM"]
    hits, probes = expected_baseline("KERNTERM", 60, same_source=True, sources=set(TARGET_NODES))
    assert term["probes_hit"] == hits
    assert result["summary"]["probes_total"] == probes
    assert term["baseline_rate"] == pytest.approx(hits / probes, abs=1e-6)
    assert term["lift"] == pytest.approx(
        (len(SAME_NODE_DELAYS) / len(TARGET_NODES)) / (hits / probes), rel=1e-3
    )


def test_a_same_timestamp_artefact_never_counts_even_without_thresholds(lab: str) -> None:
    """KERNMC is logged at the target's very second on its node: lag 0 is below the minimum lag."""
    result = call(
        "find_precursors", role="engineer", dataset="bgl", target_pattern=TARGET, min_support=1, min_lift=1
    )

    rows = _by_key(result)
    assert "KERNMC" not in rows
    assert rows[T_WARN]["support"] == 2  # with min_support 1 the weak candidate shows
    assert any("1 s" in note and "same-timestamp" in note for note in result["notes"])


def test_a_candidate_below_min_support_is_left_out_and_counted(lab: str) -> None:
    result = call("find_precursors", role="engineer", dataset="bgl", target_pattern=TARGET)

    assert T_WARN not in _by_key(result)
    summary = result["summary"]
    assert summary["candidates_total"] == 2  # KERNTERM and T_WARN were seen in a window
    assert summary["passed_total"] == 1


# ----------------------------------------------------------------- any source


def test_without_same_source_a_precursor_on_another_node_counts(lab: str) -> None:
    result = call(
        "find_precursors", role="team_lead", dataset="bgl", target_pattern=TARGET, same_source=False
    )

    rows = _by_key(result)
    term = rows["KERNTERM"]
    preceded = len(SAME_NODE_DELAYS) + len(OTHER_NODE_TARGETS)
    assert term["support"] == preceded
    assert term["rate"] == pytest.approx(preceded / len(TARGET_NODES), abs=1e-4)
    hits, probes = expected_baseline("KERNTERM", 60, same_source=False)
    assert (term["probes_hit"], result["summary"]["probes_total"]) == (hits, probes)


def test_a_pattern_that_precedes_every_probe_too_has_lift_1_and_is_left_out(lab: str) -> None:
    """T_BEAT precedes every target, but also every hourly probe: support without lift."""
    result = call(
        "find_precursors", role="team_lead", dataset="bgl", target_pattern=TARGET, same_source=False
    )
    loose = call(
        "find_precursors",
        role="team_lead",
        dataset="bgl",
        target_pattern=TARGET,
        same_source=False,
        min_lift=1,
    )

    assert T_BEAT not in _by_key(result)
    beat = _by_key(loose)[T_BEAT]
    assert beat["support"] == len(TARGET_NODES)
    assert beat["lift"] == pytest.approx(1.0, abs=0.01)


# ----------------------------------------------------------------- no precursor, scope, OpenStack


def test_no_passing_candidate_says_no_consistent_precursor_found(lab: str) -> None:
    result = call("find_precursors", role="manager", dataset="bgl", target_pattern=T_BEAT)

    assert result["rows"] == []
    assert result["total_matches"] == 0
    assert any("no consistent precursor found" in note.lower() for note in result["notes"])
    assert result["summary"]["sources_total"] == 1
    assert BEAT_NODE  # the beat runs on its own node, where nothing else is logged


def test_every_result_is_labelled_a_hypothesis_with_its_thresholds_and_baseline(lab: str) -> None:
    result = call("find_precursors", role="engineer", dataset="bgl", target_pattern=TARGET)

    text = " ".join(result["notes"])
    assert "Hypothes" in text
    assert "min_support=3" in text
    assert "min_lift=2" in text
    assert "probe" in text
    assert "median" in text


def test_the_time_range_selects_the_target_incidents(lab: str) -> None:
    result = call(
        "find_precursors",
        role="engineer",
        dataset="bgl",
        target_pattern=TARGET,
        time_from="2005-08-01",
        time_to="2005-08-04",
        min_support=1,
    )

    assert result["summary"]["incidents_total"] == 3
    assert _by_key(result)["KERNTERM"]["support"] == 3


def test_openstack_precursors_run_per_instance(lab: str) -> None:
    result = call("find_precursors", role="engineer", dataset="openstack", target_pattern=T_FAIL)

    spawn = _by_key(result)[T_SPAWN]
    assert spawn["support"] == 3
    assert (spawn["delay_median_s"], spawn["delay_min_s"], spawn["delay_max_s"]) == (20, 20, 20)


def test_an_unknown_target_pattern_is_an_actionable_error(lab: str) -> None:
    text = call_error("find_precursors", role="engineer", dataset="bgl", target_pattern="KERNRECX")

    assert "list_patterns" in text
