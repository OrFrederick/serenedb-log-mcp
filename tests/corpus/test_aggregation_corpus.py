"""Golden tests for `count_by` and `trend` over the full Base Corpus in SLM_SCHEMA (default slm).

Every number the tools return is compared with independent SQL on the same canonical tables, and
the headline totals are pinned: 4,747,963 BGL and 207,820 OpenStack Log Entries on the dev data.
The Pattern test holds both before the Pattern dictionary (T9) fills template_id and after.
"""

from __future__ import annotations

from typing import Any

import pytest
from aggregation_tools import call, independent, row_dicts

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import get_settings, load_settings
from serenedb_log_mcp.corpus import facts

pytestmark = pytest.mark.corpus

BGL_ROWS = 4_747_963
OPENSTACK_ROWS = 207_820
KERNDTLB = 152_734


@pytest.fixture(autouse=True)
def corpus_settings(slm_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=slm_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()


def within_timeout(result: dict[str, Any]) -> None:
    assert result["query_ms"] < get_settings().query_timeout_s * 1000


def test_count_by_dataset_covers_the_whole_base_corpus(slm_schema: str) -> None:
    result = call("count_by", role="manager", group_by="dataset")

    (bgl_sql, os_sql) = independent(
        slm_schema, "SELECT (SELECT count(*) FROM {bgl}), (SELECT count(*) FROM {os})"
    )[0]
    rows = row_dicts(result)
    assert [(r["dataset"], r["count"]) for r in rows] == [("bgl", BGL_ROWS), ("openstack", OPENSTACK_ROWS)]
    assert (bgl_sql, os_sql) == (BGL_ROWS, OPENSTACK_ROWS)
    assert result["summary"]["total"] == BGL_ROWS + OPENSTACK_ROWS
    assert result["data_scope"]["datasets"] == ["bgl", "openstack"]
    within_timeout(result)


def test_count_by_alert_category_matches_independent_sql(slm_schema: str) -> None:
    result = call("count_by", role="team_lead", group_by="alert_category", dataset="bgl", top=100)

    expected = independent(
        slm_schema, "SELECT coalesce(alert_tag, '(non-alert)'), count(*) FROM {bgl} GROUP BY 1"
    )
    got = {r["alert_category"]: r["count"] for r in row_dicts(result)}
    assert got == dict(expected)
    assert got["KERNDTLB"] == KERNDTLB
    assert result["truncated"] is False
    within_timeout(result)


def test_count_by_team_joins_the_ownership_table_with_exact_totals(slm_schema: str) -> None:
    result = call("count_by", role="team_lead", group_by="team", top=100)

    expected = independent(
        slm_schema,
        "SELECT 'bgl', coalesce(o.team, '(unmapped)'), count(*) FROM {bgl} e "
        "LEFT JOIN {owners} o ON o.dataset = 'bgl' AND o.component = e.component GROUP BY 2 "
        "UNION ALL SELECT 'openstack', coalesce(o.team, '(unmapped)'), count(*) FROM {os} e "
        "LEFT JOIN {owners} o ON o.dataset = 'openstack' AND o.component = e.component GROUP BY 2",
    )
    got = {(r["dataset"], r["team"]): r["count"] for r in row_dicts(result)}
    assert got == {(d, t): n for d, t, n in expected}
    assert sum(got.values()) == result["summary"]["total"] == BGL_ROWS + OPENSTACK_ROWS


def test_count_by_node_keeps_the_totals_exact_with_the_other_row(slm_schema: str) -> None:
    result = call("count_by", role="team_lead", group_by="node", dataset="bgl")

    groups = independent(slm_schema, "SELECT count(DISTINCT node) FROM {bgl}")[0][0]
    rows = row_dicts(result)
    summary = result["summary"]
    assert summary["groups_total"] == groups
    assert sum(r["count"] for r in rows) == summary["total"] == BGL_ROWS
    assert rows[-1]["node"] == "(other)"
    assert rows[-1]["count"] == summary["other_count"]
    assert result["truncated"] is True
    within_timeout(result)


def test_count_by_pattern_works_before_and_after_the_pattern_dictionary(slm_schema: str) -> None:
    result = call("count_by", role="team_lead", group_by="pattern", dataset="bgl", top=100)

    assigned = independent(slm_schema, "SELECT count(template_id) FROM {bgl}")[0][0]
    rows = row_dicts(result)
    assert sum(r["count"] for r in rows) == BGL_ROWS
    if assigned == 0:
        assert [r["pattern"] for r in rows] == ["(no Pattern)"]
        assert any("No Pattern ids are assigned in bgl" in note for note in result["notes"])
    else:
        assert result["summary"]["groups_total"] > 1


def test_trend_over_the_whole_bgl_dataset_adds_up(slm_schema: str) -> None:
    result = call("trend", role="team_lead", dataset="bgl", bucket="week")

    expected = independent(
        slm_schema, "SELECT time_bucket(INTERVAL '7 days', ts), count(*) FROM {bgl} GROUP BY 1"
    )
    assert {r[0]: r[1] for r in result["rows"] if r[1]} == {b.date().isoformat(): n for b, n in expected}
    assert sum(r[1] for r in result["rows"]) == BGL_ROWS
    assert result["total_matches"] == 32
    within_timeout(result)


def test_trend_by_alert_category_finds_the_kerndtlb_storm_day(slm_schema: str) -> None:
    result = call("trend", role="team_lead", dataset="bgl", bucket="day", group_by="alert_category", top=5)

    storm = next(s for s in result["summary"]["series"] if s["series"] == "KERNDTLB")
    peak = independent(
        slm_schema,
        "SELECT time_bucket(INTERVAL '1 day', ts) AS d, count(*) FROM {bgl} WHERE alert_tag = 'KERNDTLB' "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 1",
    )[0]
    assert storm["count"] == KERNDTLB
    assert (storm["peak_bucket"], storm["peak_count"]) == (peak[0].date().isoformat(), peak[1])
    assert storm["baseline_flag"] == "unusual_high"
    within_timeout(result)
