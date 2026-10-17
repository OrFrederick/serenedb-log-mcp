"""Golden facts about the default Incident table that `make derive` builds (T10, #1).

They read SLM_SCHEMA (default slm). Every number here was measured on the full Base Corpus
(4,747,963 BGL Log Entries) against SereneDB 26.09.1.3; see docs/results/ground-truth.md for the
published counts they are compared with. T25 re-pins them if the organisers' loader differs.

Since T9 the table holds two kinds of Incident, because the step chains every Log Entry with a
`pattern_key`: the 1,202 Incidents of the 41 BGL alert categories, and the Incidents of the
template-keyed Patterns T9 assigns to the other 4,399,503 Log Entries. Only the alert ones have a
published ground truth, and only they are stable: a template id depends on which Pattern tier T9
could use, so the Patterns, and with them the Incident count, differ between a Loghub-2.0 run and
a generic-mask run (SLM_PATTERN_TIER1=0). The golden counts below are therefore taken over the
alert categories, which the queries select with `{alerts}`. The whole table is checked for
invariants only.

The lab-corpus checks of the same step, where every expected value is obvious by construction,
live in tests/db/test_incidents_db.py.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
import pytest
from psycopg import sql

from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus.datasets import SHAPES, relation

# The default parameters of the precomputed table (#1).
GAP_S = 300
MERGE_SCOPE = "all"
# Oliner & Stearley's published BG/L result (DSN 2007; the table is reproduced in
# A. J. Oliner, "Using Influence to Understand Complex Systems", Stanford 2011, Table 2.1.2):
# 348,460 raw alerts filter down to 1,202 distinct failures over 41 alert categories.
PUBLISHED_ALERTS = 348_460
PUBLISHED_FAILURES = 1_202
PUBLISHED_CATEGORIES = 41
# The paper's own stated threshold, which does not reproduce its own count (#1).
PAPER_THRESHOLD_S = 5
PAPER_THRESHOLD_INCIDENTS = 1_643
BGL_ENTRIES = 4_747_963


def fetch(
    database_url: str, schema: str, query: str, params: list[Any] | None = None
) -> list[tuple[Any, ...]]:
    """Run `query` with {bgl}, {inc} and {alerts} bound.

    {bgl} and {inc} are the canonical Log Entry and Incident tables; {alerts} is the predicate
    that keeps the Incidents of the BGL alert categories, so the golden counts are not disturbed
    by the template-keyed Patterns T9 adds.
    """
    settings = load_settings(slm_schema=schema)
    bgl = relation("bgl", settings=settings)
    statement = sql.SQL(query).format(
        bgl=bgl.table,
        inc=bgl.incidents_table,
        alerts=sql.SQL(
            "pattern_key IN (SELECT DISTINCT alert_tag FROM {} WHERE alert_tag IS NOT NULL)"
        ).format(bgl.table),
    )
    with psycopg.connect(database_url, autocommit=True) as conn:
        return [tuple(r) for r in conn.execute(statement, params).fetchall()]


def one(database_url: str, schema: str, query: str, params: list[Any] | None = None) -> tuple[Any, ...]:
    result = fetch(database_url, schema, query, params)
    assert len(result) == 1
    return result[0]


# ----------------------------------------------------------------- the published count


def test_the_alert_categories_chain_into_the_1202_published_failures(
    database_url: str, slm_schema: str
) -> None:
    """Acceptance criterion 2: the default table holds Oliner & Stearley's 1,202 BG/L failures.

    `gap_s = 300` was chosen because it reproduces this count, so the count alone is a fitted
    result, not an independent one (docs/results/ground-truth.md says so). What is not fitted is
    that every alert Log Entry lands in exactly one Incident and that the per-category breakdown
    below agrees.
    """
    incidents, patterns, in_incidents, keyed = one(
        database_url,
        slm_schema,
        "SELECT (SELECT count(*) FROM {inc} WHERE {alerts}), "
        "(SELECT count(DISTINCT pattern_key) FROM {inc} WHERE {alerts}), "
        "(SELECT sum(entries) FROM {inc} WHERE {alerts}), "
        "(SELECT count(*) FROM {bgl} WHERE alert_tag IS NOT NULL)",
    )

    assert incidents == PUBLISHED_FAILURES
    assert patterns == PUBLISHED_CATEGORIES
    assert in_incidents == keyed == PUBLISHED_ALERTS


def test_the_published_per_category_counts_reproduce_except_two_appsev_incidents(
    database_url: str, slm_schema: str
) -> None:
    """The paper publishes the 10 largest categories plus an aggregate for the other 31.

    Nothing was fitted per category, so this is the independent part of the check: 9 of the 10
    named categories reproduce exactly, APPSEV comes out 2 lower and the aggregate 2 higher.
    APPSEV is 136 for every threshold from 250 s to 310 s, so the difference is not a threshold
    artefact; it is 2 Incidents the paper's run splits and ours does not.
    """
    # (category, published raw alerts, published distinct failures)
    published = [
        ("KERNDTLB", 152_734, 37),
        ("KERNSTOR", 63_491, 8),
        ("APPSEV", 49_651, 138),
        ("KERNMNTF", 31_531, 105),
        ("KERNTERM", 23_338, 99),
        ("KERNREC", 6_145, 9),
        ("APPREAD", 5_983, 11),
        ("KERNRTSP", 3_983, 260),
        ("APPRES", 2_370, 13),
        ("APPUNAV", 2_048, 3),
    ]
    named = [category for category, _, _ in published]
    measured = {
        str(row[0]): (int(row[1]), int(row[2]))
        for row in fetch(
            database_url,
            slm_schema,
            "SELECT pattern_key, sum(entries), count(*) FROM {inc} WHERE {alerts} GROUP BY 1",
        )
    }

    # The raw alert counts are the labels themselves, so they must match exactly.
    assert [(c, measured[c][0]) for c, _, _ in published] == [(c, raw) for c, raw, _ in published]
    off_by = {c: measured[c][1] - failures for c, _, failures in published if measured[c][1] != failures}
    assert off_by == {"APPSEV": -2}
    others = [(raw, count) for key, (raw, count) in measured.items() if key not in named]
    assert len(others) == PUBLISHED_CATEGORIES - len(published) == 31
    assert sum(raw for raw, _ in others) == 7_186
    assert sum(count for _, count in others) == 519 + 2


def test_the_papers_own_threshold_does_not_reproduce_the_papers_own_count(
    database_url: str, slm_schema: str
) -> None:
    # The paper's text states T = 5 s. That gives 1,643, not 1,202, which is why #1 settled on
    # 300 s and why the README must say that the 300 s is our inference.
    (incidents,) = one(
        database_url,
        slm_schema,
        "SELECT count(*) FILTER (WHERE previous IS NULL OR epoch_utc - previous > %s) FROM ("
        "SELECT epoch_utc, lag(epoch_utc) OVER (PARTITION BY alert_tag ORDER BY epoch_utc, entry_id) "
        "AS previous FROM {bgl} WHERE alert_tag IS NOT NULL) x",
        [PAPER_THRESHOLD_S],
    )

    assert incidents == PAPER_THRESHOLD_INCIDENTS


# ----------------------------------------------------------------- the headline contrast


def test_the_largest_kerndtlb_incident_is_one_node_shouting_for_six_hours(
    database_url: str, slm_schema: str
) -> None:
    """Acceptance criterion 3, in the ticket's words: "The largest KERNDTLB Incident has 152,182
    Log Entries on 1 node (2005-06-11). The category has 152,734 Log Entries."

    This is the project's headline: counting Log Entries puts KERNDTLB first by a wide margin,
    counting Incidents shows one node. The category still holds 37 Incidents in total, which is
    exactly the published number, so the contrast is not an artefact of the chaining.
    """
    entries, nodes, started, ended, shape, share, bursts, incidents, category_entries = one(
        database_url,
        slm_schema,
        "SELECT i.entries, i.nodes, CAST(i.t0 AS DATE), CAST(i.t1 AS DATE), i.shape, i.top_node_share, "
        "i.bursts, (SELECT count(*) FROM {inc} WHERE pattern_key = 'KERNDTLB'), "
        "(SELECT count(*) FROM {bgl} WHERE pattern_key = 'KERNDTLB') "
        "FROM {inc} i WHERE i.pattern_key = 'KERNDTLB' ORDER BY i.entries DESC LIMIT 1",
    )

    assert (entries, nodes) == (152_182, 1)
    assert started == ended == date(2005, 6, 11)
    assert category_entries == 152_734
    assert shape == "single-node storm"
    assert (share, bursts) == (1.0, 1)
    assert incidents == 37


def test_counting_log_entries_and_counting_incidents_rank_the_categories_differently(
    database_url: str, slm_schema: str
) -> None:
    # Why Incidents are the unit (#1): the loudest category by Log Entries is one node, and the
    # categories with the most Incidents are not the categories with the most Log Entries.
    by_entries = fetch(
        database_url,
        slm_schema,
        "SELECT pattern_key FROM {inc} WHERE {alerts} GROUP BY 1 ORDER BY sum(entries) DESC, 1 LIMIT 3",
    )
    by_incidents = fetch(
        database_url,
        slm_schema,
        "SELECT pattern_key FROM {inc} WHERE {alerts} GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 3",
    )

    assert [row[0] for row in by_entries] == ["KERNDTLB", "KERNSTOR", "APPSEV"]
    assert [row[0] for row in by_incidents] == ["KERNMC", "KERNRTSP", "APPSEV"]


def test_the_gb1_mount_failure_wave_is_the_worked_investigations_numbers(
    database_url: str, slm_schema: str
) -> None:
    """The #5 worked investigation: the `/p/gb1` Lustre mount-failure wave.

    The wave is a filtered set (KERNMNTF Log Entries whose message names `/p/gb1`), so its
    Incidents are chained at query time over that set, which is the #1 split between the
    precomputed default table and a filtered call. Chaining the whole KERNMNTF category instead
    gives 85 Incidents over 26,722 Log Entries in the same window, because the 5,105 Log Entries
    of the category that name no mount point join the chains.
    """
    incidents, entries, days, machine_wide, widest, first, last = one(
        database_url,
        slm_schema,
        "SELECT count(*), sum(entries), count(DISTINCT CAST(t0 AS DATE)), "
        "count(*) FILTER (WHERE nodes = 1024), max(nodes), min(t0), max(t1) FROM ("
        "SELECT min(ts) AS t0, max(ts) AS t1, count(*) AS entries, count(DISTINCT node) AS nodes FROM ("
        "SELECT ts, node, CAST(sum(CASE WHEN previous IS NULL OR epoch_utc - previous > %s "
        "THEN 1 ELSE 0 END) "
        "OVER (PARTITION BY pattern_key ORDER BY epoch_utc, entry_id ROWS UNBOUNDED PRECEDING) AS BIGINT) "
        "AS incident_id FROM ("
        "SELECT ts, node, epoch_utc, entry_id, pattern_key, "
        "lag(epoch_utc) OVER (PARTITION BY pattern_key ORDER BY epoch_utc, entry_id) AS previous "
        "FROM {bgl} WHERE pattern_key = 'KERNMNTF' AND message LIKE '%%/p/gb1%%') g) n "
        "GROUP BY incident_id) i",
        [GAP_S],
    )

    assert (incidents, entries) == (83, 26_426)
    assert (days, machine_wide, widest) == (12, 7, 1_024)
    assert (str(first), str(last)) == ("2005-08-31 18:41:43.225714", "2005-09-16 20:35:26.876154")


# ----------------------------------------------------------------- the table itself


def test_every_row_records_the_default_parameters(database_url: str, slm_schema: str) -> None:
    assert fetch(database_url, slm_schema, "SELECT DISTINCT dataset, gap_s, merge_scope FROM {inc}") == [
        ("bgl", GAP_S, MERGE_SCOPE)
    ]


def test_the_table_covers_every_log_entry_that_has_a_pattern(database_url: str, slm_schema: str) -> None:
    """Acceptance criteria 1 and 4 together: the step chains every Log Entry with a `pattern_key`
    and nothing else, so T9's template-keyed Patterns are in the table without a code change."""
    in_incidents, keyed, unkeyed, alert_keyed = one(
        database_url,
        slm_schema,
        "SELECT (SELECT sum(entries) FROM {inc}), (SELECT count(pattern_key) FROM {bgl}), "
        "(SELECT count(*) FILTER (WHERE pattern_key IS NULL) FROM {bgl}), "
        "(SELECT count(*) FROM {bgl} WHERE pattern_key = alert_tag)",
    )

    assert in_incidents == keyed
    assert keyed + unkeyed == BGL_ENTRIES
    assert alert_keyed == PUBLISHED_ALERTS
    if unkeyed:
        pytest.skip(f"T9 has not filled pattern_key for {unkeyed:,} Log Entries yet")


def test_the_precomputed_table_equals_the_same_chaining_at_query_time(
    database_url: str, slm_schema: str
) -> None:
    """The point of precomputing (#1): the table is the query-time answer, not an approximation.

    Recomputing the Incident id and the bounds straight from the Log Entries must give the same
    rows, so a tool may read the table for an unfiltered call and chain at query time for a
    filtered one without the two disagreeing.
    """
    (disagreements,) = one(
        database_url,
        slm_schema,
        "SELECT count(*) FROM ("
        "SELECT pattern_key, incident_id, count(*) AS entries, min(epoch_utc) AS t0_utc, "
        "max(epoch_utc) AS t1_utc FROM ("
        "SELECT pattern_key, epoch_utc, "
        "CAST(sum(CASE WHEN previous IS NULL OR epoch_utc - previous > %s THEN 1 ELSE 0 END) "
        "OVER (PARTITION BY pattern_key ORDER BY epoch_utc, entry_id ROWS UNBOUNDED PRECEDING) AS BIGINT) "
        "AS incident_id FROM ("
        "SELECT pattern_key, epoch_utc, entry_id, "
        "lag(epoch_utc) OVER (PARTITION BY pattern_key ORDER BY epoch_utc, entry_id) AS previous "
        "FROM {bgl} WHERE pattern_key IS NOT NULL) g) n GROUP BY pattern_key, incident_id) recomputed "
        "FULL OUTER JOIN {inc} i USING (pattern_key, incident_id) "
        "WHERE (recomputed.entries, recomputed.t0_utc, recomputed.t1_utc) "
        "IS DISTINCT FROM (i.entries, i.t0_utc, i.t1_utc)",
        [GAP_S],
    )

    assert disagreements == 0


def test_no_alert_incident_holds_a_gap_longer_than_the_threshold(
    database_url: str, slm_schema: str
) -> None:
    # An independent recompute over the alert Log Entries: inside an Incident every neighbouring
    # gap is at most 300 s, and the gap that starts the next Incident of the Pattern is longer.
    inside, between = one(
        database_url,
        slm_schema,
        "SELECT max(gap) FILTER (WHERE same), min(gap) FILTER (WHERE NOT same) FROM ("
        "SELECT epoch_utc - lag(epoch_utc) OVER w AS gap, "
        "incident_id = lag(incident_id) OVER w AS same FROM {bgl} e JOIN {inc} i "
        "ON i.pattern_key = e.pattern_key AND e.epoch_utc BETWEEN i.t0_utc AND i.t1_utc "
        "WHERE e.alert_tag IS NOT NULL "
        "WINDOW w AS (PARTITION BY e.pattern_key ORDER BY e.epoch_utc, e.entry_id)) x",
    )

    assert inside <= GAP_S
    assert between > GAP_S


def test_the_bounds_and_the_spread_hold_their_invariants(database_url: str, slm_schema: str) -> None:
    """Over the whole table: a node card is inside a midplane, an Incident has at least as many
    Log Entries as nodes and at least as many Bursts as nodes, and `t1` never precedes `t0`.

    The two levels that do not nest are a property of the node names, not of the chaining:
    `nodecards >= midplanes` fails where a link-card name such as `R27-M1-L3-U18-C` carries a
    midplane but no node card, and `midplanes >= racks` fails for the 18 Log Entries whose node
    name is a bare rack such as `R76`. `scripts/derive.py` reports both groups on every run.
    """
    bad_bounds, nodes_over, nodes_over_entries, bad_share, few_bursts = one(
        database_url,
        slm_schema,
        "SELECT count(*) FILTER (WHERE t1_utc < t0_utc OR t1 < t0), "
        "count(*) FILTER (WHERE nodes < nodecards), count(*) FILTER (WHERE nodes > entries), "
        "count(*) FILTER (WHERE top_node_share <= 0 OR top_node_share > 1), "
        "count(*) FILTER (WHERE bursts < nodes) FROM {inc}",
    )
    low, high, no_card, bare_rack = one(
        database_url,
        slm_schema,
        "SELECT min(top_node_share), max(top_node_share), "
        "count(*) FILTER (WHERE nodecards < midplanes), count(*) FILTER (WHERE midplanes < racks) "
        "FROM {inc} WHERE {alerts}",
    )

    assert (bad_bounds, nodes_over, nodes_over_entries, bad_share, few_bursts) == (0, 0, 0, 0, 0)
    assert low == pytest.approx(1 / 20_480)
    assert high == 1.0
    assert (no_card, bare_rack) == (46, 0)


def test_every_shape_label_comes_from_the_glossary_list(database_url: str, slm_schema: str) -> None:
    counted = fetch(
        database_url,
        slm_schema,
        "SELECT shape, count(*) FROM {inc} WHERE {alerts} GROUP BY 1 ORDER BY 2 DESC, 1",
    )
    (unknown,) = one(
        database_url,
        slm_schema,
        "SELECT count(*) FROM {inc} WHERE shape IS NULL OR shape NOT IN "
        "('single-node storm', 'single-node isolated', 'one midplane', 'multi-midplane', 'machine-wide')",
    )

    assert {str(shape) for shape, _ in counted} <= set(SHAPES)
    assert unknown == 0
    assert counted == [
        ("single-node isolated", 720),
        ("multi-midplane", 226),
        ("machine-wide", 136),
        ("one midplane", 119),
        ("single-node storm", 1),
    ]


def test_an_incident_on_nodes_without_topology_is_never_labelled_one_midplane(
    database_url: str, slm_schema: str
) -> None:
    """The corpus does have alert Incidents whose node names carry no topology.

    54 of them, all on the literal node name "NULL", all with one node name, so the `nodes = 1`
    branches catch them before the "one midplane" fall-through. The fall-through therefore never
    labels an Incident whose Spread is unknown. That the 89,296 Log Entries with node name "NULL"
    look like one node is a property of the data, not of the chaining; `scripts/derive.py` reports
    the count on every run.
    """
    assert fetch(
        database_url,
        slm_schema,
        "SELECT shape, count(*), count(DISTINCT nodes), max(entries) FROM {inc} "
        "WHERE {alerts} AND midplanes = 0 GROUP BY 1 ORDER BY 1",
    ) == [("single-node isolated", 54, 1, 32)]


def test_a_machine_wide_incident_really_spans_half_the_machine(
    database_url: str, slm_schema: str
) -> None:
    # BlueGene/L has 64 racks with two midplanes each. The "machine-wide" label needs 64 of those
    # 128 midplanes, so the loudest single node can never earn it. The three categories that
    # dominate the label are the ones a Manager should see: job start-up failures (APPSEV),
    # Lustre mount failures (KERNMNTF) and kernel terminations (KERNTERM).
    widest, narrowest, patterns = one(
        database_url,
        slm_schema,
        "SELECT max(midplanes), min(midplanes), count(DISTINCT pattern_key) FROM {inc} "
        "WHERE {alerts} AND shape = 'machine-wide'",
    )
    top = fetch(
        database_url,
        slm_schema,
        "SELECT pattern_key, count(*) FROM {inc} WHERE {alerts} AND shape = 'machine-wide' "
        "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 3",
    )

    assert narrowest >= 64
    assert widest == 128
    assert patterns == 11
    assert top == [("APPSEV", 51), ("KERNMNTF", 40), ("KERNTERM", 25)]


def test_openstack_has_no_incidents_until_t27(database_url: str, slm_schema: str) -> None:
    # T10 is BGL only. OpenStack Request Failure Incidents are a separate class (#1) and come in
    # T27, so no row of the table claims to be OpenStack's.
    datasets = fetch(database_url, slm_schema, "SELECT DISTINCT dataset FROM {inc}")

    assert datasets == [("bgl",)]
