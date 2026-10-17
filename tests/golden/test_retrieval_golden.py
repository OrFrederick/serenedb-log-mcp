"""Golden tests for describe_corpus, search_logs and get_context over the full Base Corpus (T5).

Fixed queries, each compared with independent SQL on the base tables (tests/golden/golden.py):
- describe_corpus: the size, time range, Components and Pattern counts of both Datasets;
- search_logs: the KERNDTLB alert category (the #5 Manager trap) and the text query "ciod failed" in
  July 2005, with their totals, first rows and facets;
- search_logs across BGL's 2005-10-30 fall-back: the same UTC time_from and time_to give the same
  total as count_by (#73);
- get_context: the ten Log Entries on each side of the first KERNDTLB entry on the storm node.
The headline numbers are pinned on the dev data: 4,747,963 BGL and 207,820 OpenStack Log Entries,
152,734 KERNDTLB Log Entries on 541 nodes (plan section 2.6).

Times in the time inputs, the Data Scope, first_seen, last_seen and describe_corpus are UTC (#65,
#73); the golden SQL reads BGL's UTC time from epoch_utc (`UTC_TS`), because its ts is US Pacific
local time. Rows keep ts as logged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from aggregation_tools import call, replay, row_dicts

from golden import Golden, words_regex
from serenedb_log_mcp.config import get_settings

pytestmark = pytest.mark.corpus

BGL_ROWS = 4_747_963
OPENSTACK_ROWS = 207_820
KERNDTLB = 152_734
KERNDTLB_NODES = 541
CIOD_FAILED_JULY = 578
"""search_logs(bgl, query='ciod failed') in July 2005 (UTC; the same 578 on local ts)."""
STORM_NODE = "R30-M0-N9-C:J16-U01"
JULY = (datetime(2005, 7, 1), datetime(2005, 8, 1))
"""UTC; bound to the golden SQL as epoch_utc values (`JULY_EPOCHS`)."""
JULY_EPOCHS = tuple(at.replace(tzinfo=UTC).timestamp() for at in JULY)
UTC_TS = "make_timestamp(CAST(round(epoch_utc * 1000000) AS BIGINT))"
"""BGL's time in UTC, written in the test: its ts is US Pacific local time (#65)."""


def within_timeout(result: dict[str, Any]) -> None:
    assert result["query_ms"] < get_settings().query_timeout_s * 1000


def test_describe_corpus_matches_independent_sql_for_both_datasets(golden_schema: str) -> None:
    golden = Golden(golden_schema)

    result = call("describe_corpus", role="engineer")

    datasets = result["summary"]["datasets"]
    assert result["data_scope"]["time_zone"] == "UTC"
    for name, table, rows, utc_ts in (
        ("bgl", "{bgl}", BGL_ROWS, UTC_TS),
        ("openstack", "{os}", OPENSTACK_ROWS, "ts"),
    ):
        described = datasets[name]
        assert described["entries"] == rows
        golden.value(described["entries"], f"SELECT count(*) FROM {table}")
        golden.value(described["first_entry"], f"SELECT min({utc_ts}) FROM {table}")
        golden.value(described["corpus_end"], f"SELECT max({utc_ts}) FROM {table}")
        golden.value(described["patterns"], f"SELECT count(DISTINCT template_id) FROM {table}")
        components = {
            r["value"]: r["entries"]
            for r in row_dicts(result)
            if r["dataset"] == name and r["kind"] == "component"
        }
        assert components == dict(golden.query(f"SELECT component, count(*) FROM {table} GROUP BY 1"))
    within_timeout(result)


def test_search_logs_by_alert_category_matches_independent_sql(golden_schema: str) -> None:
    golden = Golden(golden_schema)

    result = call("search_logs", role="engineer", dataset="bgl", alert_categories=["KERNDTLB"])

    assert golden.total(result, "SELECT count(*) FROM {bgl} WHERE alert_tag = %s", ["KERNDTLB"]) == KERNDTLB
    golden.rows(
        result,
        ["entry_id", "ts", "node", "component", "level", "alert_tag", "template_id"],
        "SELECT entry_id, ts, node, component, level, alert_tag, template_id FROM {bgl} "
        "WHERE alert_tag = %s ORDER BY epoch_utc, entry_id LIMIT 20",
        ["KERNDTLB"],
    )
    summary = result["summary"]
    assert summary["distinct_sources"] == KERNDTLB_NODES
    golden.value(
        summary["distinct_sources"],
        "SELECT count(DISTINCT node) FROM {bgl} WHERE alert_tag = %s",
        ["KERNDTLB"],
    )
    golden.value(
        summary["distinct_racks"], "SELECT count(DISTINCT rack) FROM {bgl} WHERE alert_tag = %s", ["KERNDTLB"]
    )
    golden.value(
        summary["last_seen"], f"SELECT max({UTC_TS}) FROM {{bgl}} WHERE alert_tag = %s", ["KERNDTLB"]
    )
    within_timeout(result)


def test_search_logs_by_text_matches_a_whole_word_scan_of_the_base_table(golden_schema: str) -> None:
    golden = Golden(golden_schema)
    where = (
        "regexp_matches(lower(message), %s) AND regexp_matches(lower(message), %s) "
        "AND epoch_utc >= %s AND epoch_utc < %s"
    )
    params = [words_regex("ciod"), words_regex("failed"), *JULY_EPOCHS]

    result = call(
        "search_logs",
        role="engineer",
        dataset="bgl",
        query="ciod failed",
        time_from="2005-07-01",
        time_to="2005-08-01",
    )

    assert golden.total(result, f"SELECT count(*) FROM {{bgl}} WHERE {where}", params) == CIOD_FAILED_JULY
    golden.rows(
        result,
        ["entry_id", "ts", "node"],
        f"SELECT entry_id, ts, node FROM {{bgl}} WHERE {where} ORDER BY epoch_utc, entry_id LIMIT 20",
        params,
    )
    expected = golden.query(f"SELECT component, count(*) FROM {{bgl}} WHERE {where} GROUP BY 1", params)
    assert result["summary"]["top_components"] == dict(sorted(expected, key=lambda r: (-r[1], r[0]))[:5])
    assert all("**ciod" in r["message"].lower() for r in row_dicts(result))
    within_timeout(result)


@pytest.mark.parametrize("client_binding", [False, True])
def test_a_stored_time_filtered_search_replays_to_the_published_total(
    golden_schema: str, client_binding: bool
) -> None:
    # #71: every statement of the Evidence, run as the Evidence Ledger stored it (JSON parameters).
    result = call(
        "search_logs",
        role="engineer",
        dataset="bgl",
        query="ciod failed",
        time_from="2005-07-01",
        time_to="2005-08-01",
        limit=5,
    )

    count, rows, facets = replay(result, client_binding=client_binding)

    assert result["total_matches"] == CIOD_FAILED_JULY
    assert count == [(CIOD_FAILED_JULY,)]
    assert [row[0] for row in rows] == [row[0] for row in result["rows"]]
    assert [row[2] for row in facets if row[0] == "all"] == [CIOD_FAILED_JULY]


@pytest.mark.parametrize(
    ("time_from", "time_to"),
    [
        # 00:00 PDT to 03:00 PST: four UTC hours with the repeated local hour from 01:00 inside.
        ("2005-10-30T07:00", "2005-10-30T11:00"),
        # The second copy of the repeated hour alone, named by its offset: 01:00 to 02:00 PST.
        ("2005-10-30T01:00:00-08:00", "2005-10-30T02:00:00-08:00"),
    ],
)
def test_search_logs_and_count_by_count_the_same_bgl_entries_across_the_fall_back(
    golden_schema: str, time_from: str, time_to: str
) -> None:
    golden = Golden(golden_schema)
    window = {"dataset": "bgl", "query": "error", "time_from": time_from, "time_to": time_to}

    searched = call("search_logs", role="engineer", **window)
    counted = call("count_by", role="engineer", group_by="dataset", **window)

    start, end = (searched["data_scope"][key] for key in ("time_from", "time_to"))
    expected = golden.total(
        searched,
        f"SELECT count(*) FROM {{bgl}} WHERE regexp_matches(lower(message), %s) "
        f"AND {UTC_TS} >= %s AND {UTC_TS} < %s",
        [words_regex("error"), datetime.fromisoformat(start), datetime.fromisoformat(end)],
    )
    assert expected > 0
    assert row_dicts(counted)[0]["count"] == expected
    assert (counted["data_scope"]["time_from"], counted["data_scope"]["time_to"]) == (start, end)
    assert searched["data_scope"]["time_columns"] == {"bgl": "epoch_utc"}
    assert start <= searched["summary"]["first_seen"] <= searched["summary"]["last_seen"] < end
    within_timeout(searched)


def test_get_context_returns_the_neighbours_on_the_storm_node(golden_schema: str) -> None:
    golden = Golden(golden_schema)
    anchor = golden.scalar(
        "SELECT entry_id FROM {bgl} WHERE alert_tag = 'KERNDTLB' AND node = %s "
        "ORDER BY epoch_utc, entry_id LIMIT 1",
        [STORM_NODE],
    )

    result = call("get_context", role="engineer", dataset="bgl", entry_id=anchor)

    golden.total(result, "SELECT count(*) FROM {bgl} WHERE node = %s", [STORM_NODE])
    golden.rows(
        result,
        ["entry_id", "ts", "node", "alert_tag"],
        """WITH n AS (
            SELECT entry_id, ts, node, alert_tag, epoch_utc,
                   row_number() OVER (ORDER BY epoch_utc, entry_id) AS rn
            FROM {bgl} WHERE node = %s
        ), a AS (SELECT rn FROM n WHERE entry_id = %s)
        SELECT entry_id, ts, node, alert_tag FROM n, a
        WHERE n.rn BETWEEN a.rn - 10 AND a.rn + 10 ORDER BY epoch_utc, entry_id""",
        [STORM_NODE, anchor],
    )
    rows = row_dicts(result)
    assert [r["entry_id"] for r in rows if r["anchor"]] == [anchor]
    within_timeout(result)
