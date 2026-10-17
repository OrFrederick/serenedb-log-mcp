"""Golden facts about the canonical tables that `make derive` builds from the full Base Corpus.

They read SLM_SCHEMA (default slm). T25 re-pins the numbers if the organisers' loader differs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
import pytest
from psycopg import sql

from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus.datasets import relation


def fetch(
    database_url: str, schema: str, query: str, params: list[Any] | None = None
) -> list[tuple[Any, ...]]:
    """Run `query` with {bgl}, {os}, {owners}, {bgl_idx} and {os_idx} bound to the canonical names."""
    settings = load_settings(slm_schema=schema)
    bgl, os_ = relation("bgl", settings=settings), relation("openstack", settings=settings)
    statement = sql.SQL(query).format(
        bgl=bgl.table, os=os_.table, owners=bgl.ownership_table, bgl_idx=bgl.index, os_idx=os_.index
    )
    with psycopg.connect(database_url, autocommit=True) as conn:
        return [tuple(r) for r in conn.execute(statement, params).fetchall()]


def one(database_url: str, schema: str, query: str, params: list[Any] | None = None) -> tuple[Any, ...]:
    result = fetch(database_url, schema, query, params)
    assert len(result) == 1
    return result[0]


# ----------------------------------------------------------------- row counts


def test_row_counts_match_the_raw_base_corpus(database_url: str, slm_schema: str) -> None:
    bgl, os_, owners = one(
        database_url,
        slm_schema,
        "SELECT (SELECT count(*) FROM {bgl}), (SELECT count(*) FROM {os}), (SELECT count(*) FROM {owners})",
    )

    assert bgl == 4_747_963
    assert os_ == 207_820
    assert owners == 22


def test_alert_lines_carry_their_category_as_pattern_key(database_url: str, slm_schema: str) -> None:
    alerts, keyed, kerndtlb = one(
        database_url,
        slm_schema,
        "SELECT count(alert_tag), count(*) FILTER (WHERE pattern_key = alert_tag), "
        "count(*) FILTER (WHERE alert_tag = 'KERNDTLB') FROM {bgl}",
    )

    assert alerts == keyed == 348_460
    assert kerndtlb == 152_734


# ----------------------------------------------------------------- UTC and DST (#1)


def test_epoch_utc_is_pacific_daylight_time_until_the_2005_fall_back(
    database_url: str, slm_schema: str
) -> None:
    result = fetch(
        database_url,
        slm_schema,
        "SELECT round(epoch_utc - epoch_us(ts) / 1e6)::BIGINT AS offset_s, count(*), min(ts), max(ts) "
        "FROM {bgl} GROUP BY 1 ORDER BY 1",
    )

    assert result == [
        (
            25_200,
            3_669_612,
            datetime(2005, 6, 3, 15, 42, 50, 363779),
            datetime(2005, 10, 30, 1, 18, 4, 117897),
        ),
        (28_800, 1_078_351, datetime(2005, 10, 30, 1, 20, 2, 690798), datetime(2006, 1, 4, 8, 0, 5, 233639)),
    ]


def test_local_time_order_never_contradicts_utc_order(database_url: str, slm_schema: str) -> None:
    (disagreements,) = one(
        database_url,
        slm_schema,
        "SELECT count(*) FROM (SELECT ts, lag(ts) OVER (ORDER BY epoch_utc, entry_id) AS previous "
        "FROM {bgl}) x WHERE ts < previous",
    )

    assert disagreements == 0


def test_alert_categories_chain_into_1202_incidents_at_300_s_on_utc(
    database_url: str, slm_schema: str
) -> None:
    # The #1 DST check: Oliner & Stearley's 1,202 alert Incidents reproduce with gaps on UTC time.
    (incidents,) = one(
        database_url,
        slm_schema,
        "SELECT count(*) FILTER (WHERE previous IS NULL OR epoch_utc - previous > 300) FROM ("
        "SELECT epoch_utc, lag(epoch_utc) OVER (PARTITION BY pattern_key ORDER BY epoch_utc, entry_id) "
        "AS previous FROM {bgl} WHERE alert_tag IS NOT NULL) x",
    )

    assert incidents == 1_202


def test_only_utc_gaps_split_the_fall_back_hour(database_url: str, slm_schema: str) -> None:
    # The full-corpus check that fails if a gap is ever computed on local ts: chaining every BGL
    # Log Entry at 300 s gives 8,149 chains on epoch_utc but 8,148 on ts, because on local time
    # the 3,718.57 s gap before 2005-10-30 01:20:02.690798 (PST) looks like 118.57 s.
    utc_chains, local_chains = one(
        database_url,
        slm_schema,
        "SELECT count(*) FILTER (WHERE utc_prev IS NULL OR epoch_utc - utc_prev > 300), "
        "count(*) FILTER (WHERE local_prev IS NULL OR epoch_us(ts) / 1e6 - local_prev > 300) FROM ("
        "SELECT epoch_utc, ts, lag(epoch_utc) OVER (ORDER BY epoch_utc, entry_id) AS utc_prev, "
        "lag(epoch_us(ts) / 1e6) OVER (ORDER BY epoch_us(ts), entry_id) AS local_prev FROM {bgl}) x",
    )

    assert utc_chains == 8_149
    assert local_chains == 8_148


# ----------------------------------------------------------------- topology


def test_node_names_without_a_node_card_are_counted(database_url: str, slm_schema: str) -> None:
    no_card, no_midplane, no_rack = one(
        database_url,
        slm_schema,
        "SELECT count(*) FILTER (WHERE nodecard IS NULL), count(*) FILTER (WHERE midplane IS NULL), "
        "count(*) FILTER (WHERE rack IS NULL) FROM {bgl}",
    )

    assert no_card == 121_461
    # 'NULL' (89,296), 'UNKNOWN_LOCATION' (27,039) and '-' (324) carry no location at all; 18 more
    # name a bare rack (R76 and others, in service actions), so they have a rack but no midplane.
    assert no_rack == 116_659
    assert no_midplane == 116_659 + 18


def test_kerndtlb_storm_sits_on_one_node_card(database_url: str, slm_schema: str) -> None:
    node, nodecard, midplane, rack, entries = one(
        database_url,
        slm_schema,
        "SELECT node, nodecard, midplane, rack, count(*) AS n FROM {bgl} WHERE alert_tag = 'KERNDTLB' "
        "GROUP BY ALL ORDER BY n DESC LIMIT 1",
    )

    assert (node, nodecard, midplane, rack) == ("R30-M0-N9-C:J16-U01", "R30-M0-N9", "R30-M0", "R30")
    assert entries == 152_182


# ----------------------------------------------------------------- OpenStack


def test_openstack_files_become_neutral_sources_in_load_order(database_url: str, slm_schema: str) -> None:
    result = fetch(
        database_url,
        slm_schema,
        "SELECT source_id, count(*), min(entry_id), max(entry_id) FROM {os} GROUP BY 1 ORDER BY 1",
    )

    assert result == [
        ("os-1", 52_312, 1, 52_312),
        ("os-2", 137_074, 52_313, 189_386),
        ("os-3", 18_434, 189_387, 207_820),
    ]


def test_openstack_request_columns_cover_every_api_line(database_url: str, slm_schema: str) -> None:
    lines, endpoints, statuses, latencies, instances, servers_detail = one(
        database_url,
        slm_schema,
        "SELECT count(*) FILTER (WHERE message LIKE '%status: %'), count(endpoint), count(http_status), "
        "count(latency_s), count(instance_ref), "
        "count(*) FILTER (WHERE endpoint = 'GET /v2/{{tenant}}/servers/detail') FROM {os}",
    )

    assert lines == endpoints == statuses == latencies == 98_733
    # 51,553 "[instance: ...]" lines, 4,130 GET or DELETE /servers/<uuid>, 2,065 vif-plugged events.
    assert instances == 57_748
    assert servers_detail == 67_716


# ----------------------------------------------------------------- hygiene and ownership


def test_no_canonical_text_contains_the_openstack_label(database_url: str, slm_schema: str) -> None:
    os_hits, owner_hits, bgl_other, bgl_genuine = one(
        database_url,
        slm_schema,
        "SELECT (SELECT count(*) FROM {os} WHERE lower(concat_ws(' ', source_id, log_file, component, level, "
        "request_id, instance_id, instance_ref, endpoint, message)) LIKE '%%abnormal%%'), "
        "(SELECT count(*) FROM {owners} WHERE lower(concat_ws(' ', dataset, component, team)) "
        "LIKE '%%abnormal%%'), "
        "(SELECT count(*) FROM {bgl} WHERE lower(concat_ws(' ', node, component, level, alert_tag)) "
        "LIKE '%%abnormal%%'), "
        "(SELECT count(*) FROM {bgl} WHERE message LIKE '%%exited abnormally%%')",
        [],
    )

    assert (os_hits, owner_hits, bgl_other) == (0, 0, 0)
    # BGL's own log text (the MASABNORM alert lines) is a Log Entry, not a label.
    assert bgl_genuine == 37


def test_every_component_of_the_base_corpus_has_a_team(database_url: str, slm_schema: str) -> None:
    unmapped = fetch(
        database_url,
        slm_schema,
        "SELECT e.dataset, e.component FROM (SELECT DISTINCT 'bgl' AS dataset, component FROM {bgl} "
        "UNION SELECT DISTINCT 'openstack', component FROM {os}) e LEFT JOIN {owners} o "
        "ON o.dataset = e.dataset AND o.component = e.component "
        "WHERE e.component IS NOT NULL AND o.team IS NULL",
    )

    assert unmapped == []


def test_the_ownership_stub_lists_exactly_the_components_of_the_base_corpus(
    database_url: str, slm_schema: str
) -> None:
    stale = fetch(
        database_url,
        slm_schema,
        "SELECT o.dataset, o.component FROM {owners} o LEFT JOIN ("
        "SELECT DISTINCT 'bgl' AS dataset, component FROM {bgl} "
        "UNION SELECT DISTINCT 'openstack', component FROM {os}) e "
        "ON o.dataset = e.dataset AND o.component = e.component WHERE e.component IS NULL ORDER BY 1, 2",
    )

    assert stale == []


# ----------------------------------------------------------------- indexes


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # #2 measured 350,567 for @@ 'ciod', equal to a regex scan.
        ("SELECT count(*) FROM {bgl_idx} WHERE message @@ 'ciod'", 350_567),
        ("SELECT count(*) FROM {bgl_idx} WHERE alert_tag @@ 'KERNDTLB'", 152_734),
        # Two 503 responses; a regex scan of the raw lines finds the same two.
        ("SELECT count(*) FROM {os_idx} WHERE http_status @@ ts_ge(500)", 2),
    ],
)
def test_the_indexes_answer_on_the_full_corpus(
    database_url: str, slm_schema: str, query: str, expected: int
) -> None:
    assert one(database_url, slm_schema, query) == (expected,)
