"""The Role shaping on the full Base Corpus in SLM_SCHEMA (default slm), read-only (T7).

One real question, the BGL mount failures (alert category KERNMNTF, the lead of the worked
investigation, #5), asked as each Role: the Engineer gets example Log Entries, the Team Lead three
samples, the Manager none, and all three get the same exact total, checked against independent SQL.
"""

from __future__ import annotations

import pytest
from aggregation_tools import call, independent

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts
from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.roles import NOT_MEASURED

pytestmark = pytest.mark.corpus


@pytest.fixture(autouse=True)
def corpus_settings(slm_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=slm_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.DEFAULT_ROLE_HOOK)
    facts.clear_cache()


def test_the_same_search_differs_by_role_over_the_base_corpus(slm_schema: str) -> None:
    results = {
        role: call("search_logs", role=role.value, dataset="bgl", alert_categories=["KERNMNTF"])
        for role in Role
    }

    ((expected,),) = independent(slm_schema, "SELECT count(*) FROM {bgl} WHERE alert_tag = 'KERNMNTF'")
    assert expected > 20
    assert {results[role]["total_matches"] for role in Role} == {expected}
    assert [results[role]["returned"] for role in Role] == [20, 3, 0]
    assert results[Role.MANAGER]["summary"] == results[Role.ENGINEER]["summary"]
    assert results[Role.MANAGER]["not_measured"] == list(NOT_MEASURED)
    assert results[Role.ENGINEER]["not_measured"] == []
