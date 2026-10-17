"""Golden tests for `top_patterns` over the full Base Corpus in SLM_SCHEMA (T28).

- The Manager band over the whole BGL Dataset, alert categories, is {KERNMC, APPSEV, KERNRTSP,
  KERNMNTF, KERNSOCK} (#4), and its Incidents add up to Oliner & Stearley's 1,202 failures (T10).
- Chained at query time over the whole BGL Dataset, the Incidents number 42,199, the default Incident
  table's count (T10).
- The EDRAM wording change of 2005-11-15 ("... detected and corrected" became "... detected and
  corrected over N seconds") is a drift hand-off, not the problem ending.
"""

from __future__ import annotations

from typing import Any

import pytest
from aggregation_tools import call, row_dicts

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import get_settings, load_settings
from serenedb_log_mcp.corpus import facts

pytestmark = pytest.mark.corpus

BAND = {"KERNMC", "APPSEV", "KERNRTSP", "KERNMNTF", "KERNSOCK"}
ALERT_INCIDENTS = 1_202
WHOLE_BGL_INCIDENTS = 42_199
EDRAM_OLD = "bf80d2d972981345"
"""`<*> L3 <*> error(s) (dcr <*>) detected and corrected`, 2005-06-03 to 2005-11-21."""
EDRAM_NEW = "be09e74e392f5063"
"""The same with `over <*> seconds`, from 2005-11-15."""


@pytest.fixture(autouse=True)
def corpus_settings(slm_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=slm_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()


def within_timeout(result: dict[str, Any]) -> None:
    assert result["query_ms"] < get_settings().query_timeout_s * 1000


def test_the_manager_band_over_the_whole_bgl_dataset() -> None:
    result = call("top_patterns", role="manager", dataset="bgl", sort="priority")
    rows = row_dicts(result)

    assert {r["pattern"] for r in rows if r["band"]} == BAND
    assert [r["band"] for r in rows[:6]] == [True] * 5 + [False]
    assert result["summary"]["datasets"]["bgl"]["incidents_total"] == ALERT_INCIDENTS
    assert result["summary"]["datasets"]["bgl"]["patterns_total"] == 41
    within_timeout(result)


def test_query_time_incidents_over_the_whole_bgl_dataset() -> None:
    # 42,199 is the default Incident table's row count for BGL (T10, #86): chained at query time over
    # the whole Dataset with the same gap, the Incidents are the same.
    result = call("top_patterns", role="team_lead", dataset="bgl", sort="incidents")

    assert result["summary"]["datasets"]["bgl"]["incidents_total"] == WHOLE_BGL_INCIDENTS
    assert result["summary"]["datasets"]["bgl"]["entries_total"] == 4_747_963
    within_timeout(result)


def test_the_edram_wording_change_is_a_handoff() -> None:
    result = call("top_patterns", role="team_lead", dataset="bgl", pattern_ids=[EDRAM_OLD, EDRAM_NEW])
    rows = {r["pattern"]: r for r in row_dicts(result)}

    assert rows[EDRAM_OLD]["handoff_to"] == EDRAM_NEW
    assert rows[EDRAM_NEW]["handoff_from"] == EDRAM_OLD
    assert rows[EDRAM_NEW]["first_seen"].startswith("2005-11-15")
    assert rows[EDRAM_OLD]["template_text"] == "<*> L3 <*> error(s) (dcr <*>) detected and corrected"


def test_openstack_priority_ranks_request_failure_patterns() -> None:
    result = call("top_patterns", role="manager", dataset="openstack", sort="priority")
    rows = row_dicts(result)

    assert rows
    assert sum(r["band"] for r in rows) == min(5, len(rows))
    assert all(r["kind"] == "template" for r in rows)
    within_timeout(result)
