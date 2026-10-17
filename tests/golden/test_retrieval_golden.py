"""Golden tests for describe_corpus, search_logs and get_context over the full Base Corpus (T5).

Fixed queries, each compared with independent SQL on the base tables (tests/golden/golden.py):
- describe_corpus: the size, time range, Components and Pattern counts of both Datasets;
- search_logs: the KERNDTLB alert category (the #5 Manager trap) and the text query "ciod failed" in
  July 2005, with their totals, first rows and facets;
- get_context: the ten Log Entries on each side of the first KERNDTLB entry on the storm node.
The headline numbers are pinned on the dev data: 4,747,963 BGL and 207,820 OpenStack Log Entries,
152,734 KERNDTLB Log Entries on 541 nodes (plan section 2.6).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from aggregation_tools import call, row_dicts

from golden import Golden, words_regex
from serenedb_log_mcp.config import get_settings

pytestmark = pytest.mark.corpus

BGL_ROWS = 4_747_963
OPENSTACK_ROWS = 207_820
KERNDTLB = 152_734
KERNDTLB_NODES = 541
STORM_NODE = "R30-M0-N9-C:J16-U01"
JULY = (datetime(2005, 7, 1), datetime(2005, 8, 1))


def within_timeout(result: dict[str, Any]) -> None:
    assert result["query_ms"] < get_settings().query_timeout_s * 1000


def test_describe_corpus_matches_independent_sql_for_both_datasets(golden_schema: str) -> None:
    golden = Golden(golden_schema)

    result = call("describe_corpus", role="engineer")

    datasets = result["summary"]["datasets"]
    for name, table, rows in (("bgl", "{bgl}", BGL_ROWS), ("openstack", "{os}", OPENSTACK_ROWS)):
        described = datasets[name]
        assert described["entries"] == rows
        golden.value(described["entries"], f"SELECT count(*) FROM {table}")
        golden.value(described["first_entry"], f"SELECT min(ts) FROM {table}")
        golden.value(described["corpus_end"], f"SELECT max(ts) FROM {table}")
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
    golden.value(summary["last_seen"], "SELECT max(ts) FROM {bgl} WHERE alert_tag = %s", ["KERNDTLB"])
    within_timeout(result)


def test_search_logs_by_text_matches_a_whole_word_scan_of_the_base_table(golden_schema: str) -> None:
    golden = Golden(golden_schema)
    where = (
        "regexp_matches(lower(message), %s) AND regexp_matches(lower(message), %s) AND ts >= %s AND ts < %s"
    )
    params = [words_regex("ciod"), words_regex("failed"), *JULY]

    result = call(
        "search_logs",
        role="engineer",
        dataset="bgl",
        query="ciod failed",
        time_from="2005-07-01",
        time_to="2005-08-01",
    )

    golden.total(result, f"SELECT count(*) FROM {{bgl}} WHERE {where}", params)
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
