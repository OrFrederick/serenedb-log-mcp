"""`search_logs` end to end through MCP, on the synthetic corpus of tests/support/retrieval_corpus.py.

The totals are known by construction and also checked against independent SQL on the base table,
so the tool cannot pass by agreeing with itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aggregation_corpus import (
    APPSEV,
    F_DRIFT,
    MMCS,
    NEWCOMP,
    NODE_B,
    PACIFIC,
    STEADY,
    STORM,
    STORM_START,
    T_TLB,
    U1,
)
from aggregation_tools import call, call_error, independent, replay, row_dicts
from retrieval_corpus import DST_MESSAGES, DST_TS

pytestmark = pytest.mark.db

STORM_END = STORM_START + timedelta(seconds=30 * (STORM - 1))


def utc(logged: datetime) -> datetime:
    """A logged BGL ts (US Pacific) in UTC, the clock of search_logs' time inputs and facets (#73)."""
    return logged.replace(tzinfo=PACIFIC).astimezone(UTC).replace(tzinfo=None)


def epoch(at: datetime) -> float:
    """A UTC time as an epoch_utc value."""
    return at.replace(tzinfo=UTC).timestamp()


def test_all_words_finds_every_matching_entry_with_an_exact_total_and_a_sample_of_rows(corpus: str) -> None:
    result = call("search_logs", role="engineer", dataset="bgl", query="TLB error")

    (expected,) = independent(
        corpus, "SELECT count(*) FROM {bgl} WHERE message = 'data TLB error interrupt'"
    )[0]
    assert result["total_matches"] == expected == STORM
    assert result["returned"] == 20
    assert result["truncated"] is True
    rows = row_dicts(result)
    assert result["columns"] == [
        "entry_id",
        "ts",
        "node",
        "component",
        "level",
        "alert_tag",
        "template_id",
        "message",
    ]
    assert rows[0]["ts"] == STORM_START.isoformat()
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)
    assert rows[0]["message"] == "data **TLB** **error** interrupt"  # HighlightAll marks each word
    assert {r["node"] for r in rows} == {NODE_B}
    assert any("not a frequency" in note for note in result["notes"])


def test_the_facets_describe_every_match_not_only_the_returned_rows(corpus: str) -> None:
    summary = call("search_logs", role="engineer", dataset="bgl", query="TLB error", limit=5)["summary"]

    assert summary["entries_total"] == STORM
    assert summary["distinct_sources"] == 1
    assert summary["distinct_racks"] == 1
    assert summary["first_seen"] == utc(STORM_START).isoformat() == "2005-07-20T17:00:00"
    assert summary["last_seen"] == utc(STORM_END).isoformat()
    assert summary["top_components"] == {"KERNEL": STORM}
    assert summary["top_levels"] == {"FATAL": STORM}


def test_newest_first_starts_at_the_last_match(corpus: str) -> None:
    rows = row_dicts(call("search_logs", role="engineer", dataset="bgl", query="TLB", sort="newest", limit=3))

    assert [r["ts"] for r in rows] == [(STORM_END - timedelta(seconds=30 * i)).isoformat() for i in range(3)]


def test_relevance_returns_scores_sorted_by_bm25_alone(corpus: str) -> None:
    result = call("search_logs", role="engineer", dataset="bgl", query="ciod failed", sort="relevance")

    assert result["total_matches"] == APPSEV
    scores = [r["score"] for r in row_dicts(result)]
    assert scores == sorted(scores, reverse=True)
    assert all(isinstance(score, float) for score in scores)
    assert result["sql"][1].endswith("ORDER BY BM25(e.tableoid) DESC LIMIT %s")
    assert any("equal scores" in note for note in result["notes"])


def test_phrase_needs_the_words_next_to_each_other_in_order(corpus: str) -> None:
    found = call("search_logs", role="engineer", dataset="bgl", query="failed to read", mode="phrase")
    reversed_ = call("search_logs", role="engineer", dataset="bgl", query="read failed", mode="phrase")

    assert found["total_matches"] == APPSEV
    assert reversed_["total_matches"] == 0


def test_any_words_needs_one_of_the_words(corpus: str) -> None:
    result = call("search_logs", role="engineer", dataset="bgl", query="idoproxy hello", mode="any_words")

    assert result["total_matches"] == MMCS + NEWCOMP


def test_fuzzy_tolerates_one_typo_per_word(corpus: str) -> None:
    result = call("search_logs", role="engineer", dataset="bgl", query="interupt", mode="fuzzy")

    assert result["total_matches"] == STORM
    assert row_dicts(result)[0]["message"] == "data TLB error **interrupt**"


def test_pattern_ids_alone_find_the_entries_of_a_pattern_without_a_query(corpus: str) -> None:
    result = call("search_logs", role="engineer", dataset="bgl", pattern_ids=[T_TLB])

    assert result["total_matches"] == STORM
    assert row_dicts(result)[0]["message"] == "data TLB error interrupt"
    assert result["data_scope"]["filters"]["pattern_ids"] == [T_TLB]


def test_a_pattern_family_finds_every_pattern_of_the_family(corpus: str) -> None:
    result = call("search_logs", role="team_lead", dataset="bgl", family_id=F_DRIFT)

    assert result["total_matches"] == STEADY


def test_an_alert_category_counts_as_a_pattern_filter(corpus: str) -> None:
    result = call("search_logs", role="engineer", dataset="bgl", alert_categories=["KERNDTLB"])

    assert result["total_matches"] == STORM


def test_component_level_and_time_filters_narrow_the_matches(corpus: str) -> None:
    july = call(
        "search_logs",
        role="engineer",
        dataset="bgl",
        query="steady",
        time_from="2005-07-01",
        time_to="2005-08-01",
    )
    app = call(
        "search_logs", role="engineer", dataset="bgl", query="ciod", components=["APP"], levels=["fatal"]
    )
    kernel = call("search_logs", role="engineer", dataset="bgl", query="ciod", components=["KERNEL"])

    (july_sql,) = independent(
        corpus,
        "SELECT count(*) FROM {bgl} WHERE message LIKE 'steady%%' AND epoch_utc >= %s AND epoch_utc < %s",
        [epoch(datetime(2005, 7, 1)), epoch(datetime(2005, 8, 1))],
    )[0]
    assert july["total_matches"] == july_sql == 62
    assert july["data_scope"]["time_zone"] == "UTC"
    assert july["data_scope"]["time_columns"] == {"bgl": "epoch_utc"}
    assert app["total_matches"] == APPSEV
    assert app["data_scope"]["filters"]["levels"] == ["FATAL"]
    assert kernel["total_matches"] == 0


def test_a_relative_time_resolves_against_the_corpus_end(corpus: str) -> None:
    result = call("search_logs", role="engineer", dataset="bgl", query="steady", time_from="last 7d")

    scope = result["data_scope"]
    assert scope["time_input"] == "last 7d"
    # The corpus end in UTC: the last Log Entry is 2005-07-31 14:00 PDT.
    assert scope["resolved_against"] == "bgl corpus end 2005-07-31T21:00:00"
    assert scope["time_to"] == "2005-07-31T21:00:01"
    assert result["total_matches"] == 14


def test_a_logged_ts_that_repeats_is_two_utc_times_an_hour_apart(corpus: str) -> None:
    # The two DST lines share one logged ts but lie an hour apart in epoch_utc; a UTC range of
    # the first hour after the logged time selects the first line only.
    first = utc(DST_TS)
    one = call(
        "search_logs",
        role="engineer",
        dataset="bgl",
        query="fall back",
        time_from=first.isoformat(),
        time_to=(first + timedelta(hours=1)).isoformat(),
    )
    both = call("search_logs", role="engineer", dataset="bgl", query="fall back")

    assert [row["ts"] for row in row_dicts(one)] == [DST_TS.isoformat()]
    assert "before" in row_dicts(one)[0]["message"]
    assert both["total_matches"] == len(DST_MESSAGES)
    assert (both["summary"]["first_seen"], both["summary"]["last_seen"]) == (
        first.isoformat(),
        (first + timedelta(hours=1)).isoformat(),
    )


def test_openstack_sources_are_instances(corpus: str) -> None:
    result = call("search_logs", role="engineer", dataset="openstack", query="instance", sources=[U1])

    assert result["total_matches"] == 14
    assert result["columns"][4:6] == ["request_id", "instance_ref"]
    assert result["summary"]["distinct_sources"] == 1
    assert result["summary"]["distinct_racks"] is None


def test_zero_matches_is_a_result_with_a_hint_not_an_error(corpus: str) -> None:
    result = call("search_logs", role="engineer", dataset="bgl", query="nonexistentword")

    assert result["total_matches"] == 0
    assert result["rows"] == []
    assert any("mode='fuzzy'" in note for note in result["notes"])
    assert len(result["sql"]) == 1  # no row query and no facets when nothing matched


def test_a_search_needs_a_query_or_a_pattern_filter(corpus: str) -> None:
    text = call_error("search_logs", role="engineer", dataset="bgl", components=["KERNEL"])

    assert "query" in text
    assert "pattern_ids" in text
    assert "count_by" in text


def test_relevance_needs_a_query(corpus: str) -> None:
    text = call_error("search_logs", role="engineer", dataset="bgl", pattern_ids=[T_TLB], sort="relevance")

    assert "sort='relevance'" in text


def test_a_misspelt_component_gets_a_did_you_mean_hint(corpus: str) -> None:
    text = call_error("search_logs", role="engineer", dataset="bgl", query="ciod", components=["KERNAL"])

    assert "Did you mean 'KERNEL'" in text


def test_alert_categories_exist_only_in_bgl(corpus: str) -> None:
    text = call_error("search_logs", role="engineer", dataset="openstack", alert_categories=["KERNDTLB"])

    assert "BGL" in text


def test_the_limit_caps_the_rows(corpus: str) -> None:
    assert call("search_logs", role="engineer", dataset="bgl", query="TLB", limit=5)["returned"] == 5
    assert "50" in call_error("search_logs", role="engineer", dataset="bgl", query="TLB", limit=51)


def test_the_evidence_holds_the_count_rows_and_facets_statements_on_the_index(corpus: str) -> None:
    result = call("search_logs", role="engineer", dataset="bgl", query="TLB")

    assert len(result["sql"]) == len(result["params"]) == 3
    assert result["sql"][0].startswith("SELECT count(*) FROM")
    assert all("bgl_entry_idx" in statement for statement in result["sql"])
    assert result["params"][1][-1] == 20  # the LIMIT


@pytest.mark.parametrize("client_binding", [False, True])
@pytest.mark.parametrize(
    ("dataset", "query", "time_from", "time_to"),
    [
        # BGL's range is a range of epoch_utc (DOUBLE), OpenStack's a range of ts (TIMESTAMP).
        ("bgl", "steady", "2005-07-01", "2005-08-01"),
        ("openstack", "instance", "2017-05-15", "2017-05-16T12:00:00"),
    ],
)
def test_a_time_filtered_search_replays_from_the_evidence_ledger_as_stored(
    corpus: str, dataset: str, query: str, time_from: str, time_to: str, client_binding: bool
) -> None:
    # #71: the ledger stores parameters as JSON, so an uncast ts_lt(%s) got an ISO string and failed.
    result = call(
        "search_logs",
        role="engineer",
        dataset=dataset,
        query=query,
        time_from=time_from,
        time_to=time_to,
        limit=5,
    )

    count, rows, facets = replay(result, client_binding=client_binding)

    assert result["total_matches"] > result["returned"] == 5
    assert count == [(result["total_matches"],)]
    assert [row[0] for row in rows] == [row[0] for row in result["rows"]]
    assert [row[2] for row in facets if row[0] == "all"] == [result["total_matches"]]
