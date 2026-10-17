"""Golden checks of `find_precursors` and `list_incidents(similar_to=...)` on the full Base Corpus (T16).

The numbers come from the #1 and #4 research (KERNMC before APPSEV; the KERNTERM to KERNREC wave of
2005-11-15) and from independent SQL on the canonical tables, so the tool cannot pass by agreeing
with itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from aggregation_tools import call, row_dicts

from golden import Golden

pytestmark = pytest.mark.corpus


def _by_key(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["precursor"]: row for row in row_dicts(result)}


def test_kernmc_within_120_s_precedes_64_of_136_appsev_incidents(golden_schema: str) -> None:
    """Acceptance criterion 4: "KERNMC within 120 s precedes 64 of 136 APPSEV Incidents (47%)"."""
    result = call(
        "find_precursors",
        role="team_lead",
        dataset="bgl",
        target_pattern="APPSEV",
        window_s=120,
        same_source=False,
    )

    kernmc = _by_key(result)["KERNMC"]
    assert result["summary"]["incidents_total"] == 136
    assert kernmc["support"] == 64
    assert kernmc["rate"] == pytest.approx(0.4706, abs=1e-4)
    assert kernmc["lift"] > 100  # a few hourly probes in 7 months see a KERNMC
    Golden(golden_schema).value(
        kernmc["support"],
        "SELECT count(*) FROM (SELECT epoch_utc AS t0 FROM (SELECT epoch_utc, lag(epoch_utc) OVER "
        "(ORDER BY epoch_utc, entry_id) AS previous FROM {bgl} WHERE alert_tag = 'APPSEV') chained "
        "WHERE previous IS NULL OR epoch_utc - previous > 300) i "
        "WHERE EXISTS (SELECT 1 FROM {bgl} e WHERE e.alert_tag = 'KERNMC' "
        "AND e.epoch_utc BETWEEN i.t0 - 120 AND i.t0 - 1)",
    )


def test_kernterm_precedes_kernrec_on_each_node_by_85_s_median(golden_schema: str) -> None:
    """Acceptance criterion 4: "KERNTERM to KERNREC has a per-node median delay of 85 s (range 47 to
    142 s)", the wave of 2005-11-15 on 2,048 nodes."""
    result = call(
        "find_precursors",
        role="engineer",
        dataset="bgl",
        target_pattern="KERNREC",
        window_s=150,
        # The local day of the wave: time inputs are UTC (#74), and BGL logs US Pacific time (PST).
        time_from="2005-11-15T00:00:00-08:00",
        time_to="2005-11-16T00:00:00-08:00",
        min_support=1,
    )

    kernterm = _by_key(result)["KERNTERM"]
    assert kernterm["units_preceded"] == 2048
    assert round(kernterm["delay_median_s"]) == 85
    assert (round(kernterm["delay_min_s"]), round(kernterm["delay_max_s"])) == (47, 142)


def test_the_kerndtlb_wave_repeats_the_one_half_an_hour_earlier(golden_schema: str) -> None:
    """Acceptance criterion 6: a known repeat from the #7 research, where the nearest earlier Incident
    by template-set Jaccard has the same alert category (hit@1 0.984). On 2005-06-05 two KERNDTLB
    waves hit the same 76 nodes about half an hour apart."""
    result = call(
        "list_incidents", role="engineer", dataset="bgl", pattern_key="KERNDTLB", similar_to=2, limit=5
    )

    top = row_dicts(result)[0]
    assert (top["pattern_key"], top["incident_id"]) == ("KERNDTLB", 1)
    assert (top["jaccard"], top["same_component"], top["nodes"]) == (1, True, 76)
    assert result["summary"]["reference"]["nodes"] == 76
    assert any("Jaccard" in note and "BM25" in note for note in result["notes"])


def test_the_kernterm_wave_of_2005_11_15_looks_like_an_earlier_kernterm_wave(golden_schema: str) -> None:
    result = call(
        "list_incidents", role="engineer", dataset="bgl", pattern_key="KERNTERM", similar_to=98, limit=3
    )

    rows = row_dicts(result)
    assert result["summary"]["reference"]["nodes"] == 2048
    assert all(row["pattern_key"] == "KERNTERM" and row["jaccard"] == 1 for row in rows)
    # The tie-break on Spread puts the earlier wave with the most nodes first: 1,024 on 2005-10-28.
    assert (rows[0]["incident_id"], rows[0]["nodes"]) == (92, 1024)
