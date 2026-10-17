"""`search_logs` end to end through MCP, on the synthetic corpus of tests/support/retrieval_corpus.py.

The totals are known by construction and also checked against independent SQL on the base table,
so the tool cannot pass by agreeing with itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from aggregation_corpus import (
    APPSEV,
    F_DRIFT,
    MMCS,
    NEWCOMP,
    NODE_B,
    STEADY,
    STORM,
    STORM_START,
    T_TLB,
    U1,
)
from aggregation_tools import call, call_error, independent, row_dicts

pytestmark = pytest.mark.db

STORM_END = STORM_START + timedelta(seconds=30 * (STORM - 1))


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
    assert summary["first_seen"] == STORM_START.isoformat()
    assert summary["last_seen"] == STORM_END.isoformat()
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
        "SELECT count(*) FROM {bgl} WHERE message LIKE 'steady%%' AND ts >= %s AND ts < %s",
        [datetime(2005, 7, 1), datetime(2005, 8, 1)],
    )[0]
    assert july["total_matches"] == july_sql == 62
    assert app["total_matches"] == APPSEV
    assert app["data_scope"]["filters"]["levels"] == ["FATAL"]
    assert kernel["total_matches"] == 0


def test_a_relative_time_resolves_against_the_corpus_end(corpus: str) -> None:
    result = call("search_logs", role="engineer", dataset="bgl", query="steady", time_from="last 7d")

    scope = result["data_scope"]
    assert scope["time_input"] == "last 7d"
    assert scope["resolved_against"].startswith("bgl corpus end 2005-07-31")
    assert result["total_matches"] == 14


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
