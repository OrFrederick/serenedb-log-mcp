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
UTC_TS = "make_timestamp(CAST(round(epoch_utc * 1000000) AS BIGINT))"
"""BGL's time in UTC, independently of the tools: its ts is US Pacific local time (#65)."""


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
    assert not [r for r in rows if r["baseline_flag"].startswith("unusual")]
    if assigned == 0:
        assert [r["pattern"] for r in rows] == ["(no Pattern)"]
        assert any("No Pattern ids are assigned in bgl" in note for note in result["notes"])
    else:
        assert result["summary"]["groups_total"] > 1


def test_trend_over_the_whole_bgl_dataset_adds_up(slm_schema: str) -> None:
    result = call("trend", role="team_lead", dataset="bgl", bucket="week")

    expected = independent(
        slm_schema, f"SELECT time_bucket(INTERVAL '7 days', {UTC_TS}), count(*) FROM {{bgl}} GROUP BY 1"
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
        f"SELECT time_bucket(INTERVAL '1 day', {UTC_TS}) AS d, count(*) FROM {{bgl}} "
        "WHERE alert_tag = 'KERNDTLB' GROUP BY 1 ORDER BY 2 DESC LIMIT 1",
    )[0]
    assert storm["count"] == KERNDTLB
    assert (storm["peak_bucket"], storm["peak_count"]) == (peak[0].date().isoformat(), peak[1])
    # The storm runs 17:17 to 23:39 local time on 2005-06-11, which is 2005-06-12 in UTC.
    assert storm["peak_bucket"] == "2005-06-12"
    # A whole-Dataset trend has no week outside its scope to compare with.
    assert storm["baseline_flag"] == "no_baseline"
    within_timeout(result)


def test_a_week_of_bgl_is_judged_against_the_weeks_outside_it(slm_schema: str) -> None:
    result = call(
        "count_by",
        role="team_lead",
        group_by="alert_category",
        dataset="bgl",
        time_from="2005-06-06",
        time_to="2005-06-13",
        top=100,
    )

    rows = {r["alert_category"]: r for r in row_dicts(result)}
    weeks = result["summary"]["baseline"]["bgl"]["whole_weeks_outside_scope"]
    assert weeks == 29  # 30 whole weeks in BGL, minus the one in the Data Scope
    assert rows["(non-alert)"]["median_week"] is not None
    assert rows["(non-alert)"]["baseline_basis"] == "median_week"


def test_an_hourly_trend_across_the_2005_fall_back_counts_each_utc_hour_once(slm_schema: str) -> None:
    result = call(
        "trend",
        role="engineer",
        dataset="bgl",
        bucket="hour",
        time_from="2005-10-30T07:00",
        time_to="2005-10-30T11:00",
    )

    utc = independent(
        slm_schema,
        f"SELECT time_bucket(INTERVAL '1 hour', {UTC_TS}), count(*) FROM {{bgl}} "
        f"WHERE {UTC_TS} >= TIMESTAMP '2005-10-30 07:00:00' AND {UTC_TS} < TIMESTAMP '2005-10-30 11:00:00' "
        "GROUP BY 1 ORDER BY 1",
    )
    # On local ts the hour from 01:00 (08:00 to 09:59 UTC) is one bucket of 49 Log Entries.
    local = independent(
        slm_schema,
        "SELECT count(*) FROM {bgl} WHERE ts >= TIMESTAMP '2005-10-30 01:00:00' "
        "AND ts < TIMESTAMP '2005-10-30 02:00:00'",
    )[0][0]
    assert result["rows"] == [[b.strftime("%Y-%m-%dT%H:%M"), n] for b, n in utc]
    assert [n for _, n in result["rows"]] == [472, 11, 38, 26]
    assert local == 11 + 38
    assert result["data_scope"]["time_columns"] == {"bgl": "epoch_utc"}
    within_timeout(result)
