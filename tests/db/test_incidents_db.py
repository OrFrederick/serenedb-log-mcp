"""The Incident derive step against SereneDB (T10, #1 Decision proposal).

These tests run on a handcrafted lab corpus, so every Incident, Burst, Spread value and Shape
label has one obvious expected value. The golden numbers of the real Base Corpus live in
tests/corpus/test_incidents_corpus.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

import derive
from serenedb_log_mcp.corpus.datasets import INCIDENT_COLUMNS, SHAPES

STEP = Path(derive.SQL_DIR) / "derive" / "30_incidents.sql"
# Any UTC second inside PDT; the lab's local timestamps are this minus seven hours.
BASE_EPOCH = 1_118_500_000
PDT_OFFSET_S = 25_200
GAP_S = 300


def _node(rack: int, midplane: int, card: int = 0) -> str:
    return f"R{rack:02d}-M{midplane}-N{card}-C:J01-U01"


STORM_NODE = _node(10, 0, 4)
PAIR_A, PAIR_B = _node(11, 0, 0), _node(11, 0, 1)
MIDPLANE_A, MIDPLANE_B = _node(12, 0), _node(12, 1)


def _lab_rows() -> list[tuple[Any, ...]]:
    """The lab corpus: one Pattern per case, so every expected value is obvious.

    Each row is (line_no, alert_tag, epoch, ts, node, component, level, message). `ts` is the
    local time of `epoch` in PDT, except for LABDST, where two rows share one local time an hour
    apart in UTC: the fall-back case that separates a gap on `epoch_utc` from one on `ts`.
    """
    rows: list[tuple[Any, ...]] = []

    def add(tag: str, offset: float, node: str, *, local_offset: float | None = None) -> None:
        epoch = BASE_EPOCH + offset
        local = BASE_EPOCH + (offset if local_offset is None else local_offset) - PDT_OFFSET_S
        rows.append(
            (
                len(rows) + 1,
                tag,
                int(epoch),
                datetime.fromtimestamp(local, UTC).replace(tzinfo=None),
                node,
                "KERNEL",
                "FATAL",
                f"{tag} on {node}",
            )
        )

    # One node, 120 Log Entries 10 s apart: one Incident, one Burst, a single-node storm.
    for i in range(120):
        add("LABSTORM", 10 * i, STORM_NODE)
    # One Log Entry on its own.
    add("LABISO", 0, STORM_NODE)
    # 400 s apart on one node: the gap is longer than 300 s, so these are two Incidents.
    add("LABGAP", 0, STORM_NODE)
    add("LABGAP", 400, STORM_NODE)
    # Two nodes of one midplane. Merged across nodes the chain holds (two 200 s gaps), but on
    # PAIR_A alone the gap is 400 s, so the Incident has three Bursts on two nodes.
    add("LABBURST", 0, PAIR_A)
    add("LABBURST", 200, PAIR_B)
    add("LABBURST", 400, PAIR_A)
    # The fall-back: one local time, two UTC seconds an hour apart. On `ts` the gap looks like 0 s
    # and gives one Incident; on `epoch_utc` it is 3,600 s and gives two.
    add("LABDST", 0, PAIR_A, local_offset=0)
    add("LABDST", 3600, PAIR_A, local_offset=0)
    # Two midplanes of one rack.
    add("LABMID", 0, MIDPLANE_A)
    add("LABMID", 100, MIDPLANE_B)
    # 64 of BlueGene/L's 128 midplanes, one Log Entry each: machine-wide.
    for i in range(64):
        add("LABWIDE", i, _node(i // 2, i % 2))
    # Node names that carry no topology at all, so the Incident reports 0 midplanes.
    add("LABNOTOPO", 0, "NULL")
    add("LABNOTOPO", 10, "UNKNOWN_LOCATION")
    # Non-alert lines. They carry no alert category, so T9's Pattern dictionary gives them a
    # template id, and the step chains them on that: one more Incident, with no code change.
    for i in range(5):
        add("-", 10 * i, PAIR_B)
    return rows


LAB_ROWS = _lab_rows()
# The Incidents of the alert categories, which is what the LAB* cases above are about, and the one
# Incident the five non-alert Log Entries form once T9 has keyed them.
LAB_ALERT_INCIDENTS = 10
LAB_NON_ALERT_ENTRIES = 5
# The Incidents of the BGL alert categories: `pattern_key` is the category, not a template id.
ALERTS = "pattern_key IN (SELECT DISTINCT alert_tag FROM {bgl} WHERE alert_tag IS NOT NULL)"


@dataclass(frozen=True)
class Lab:
    raw: str
    slm: str


def _fetch(database_url: str, query: sql.Composable, params: list[Any] | None = None) -> list[Any]:
    with psycopg.connect(database_url, autocommit=True) as conn:
        return [tuple(row) for row in conn.execute(query, params).fetchall()]


def _one(database_url: str, query: sql.Composable, params: list[Any] | None = None) -> tuple[Any, ...]:
    rows = _fetch(database_url, query, params)
    assert len(rows) == 1
    return rows[0]  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def lab(database_url: str) -> Iterator[Lab]:
    """The lab corpus loaded as raw tables and derived into its own schema; dropped afterwards."""
    suffix = uuid.uuid4().hex[:8]
    schemas = Lab(raw=f"scratch_t10_{suffix}_raw", slm=f"scratch_t10_{suffix}")
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schemas.raw)))
            conn.execute(
                sql.SQL(
                    "CREATE TABLE {} (line_no BIGINT, alert_tag VARCHAR, epoch BIGINT, ts TIMESTAMP, "
                    "node VARCHAR, component VARCHAR, level VARCHAR, message VARCHAR, raw VARCHAR)"
                ).format(sql.Identifier(schemas.raw, "bgl"))
            )
            for row in LAB_ROWS:
                conn.execute(
                    sql.SQL(
                        "INSERT INTO {} (line_no, alert_tag, epoch, ts, node, component, level, message, "
                        "raw) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    ).format(sql.Identifier(schemas.raw, "bgl")),
                    [*row, "lab"],
                )
            # The derive step stops when a canonical entry table is empty, so the lab carries one
            # OpenStack Log Entry. OpenStack Incidents are T27's.
            conn.execute(
                sql.SQL(
                    "CREATE TABLE {} (line_no BIGINT, source_file VARCHAR, log_file VARCHAR, "
                    "ts TIMESTAMP, pid BIGINT, level VARCHAR, component VARCHAR, request_id VARCHAR, "
                    "instance_id VARCHAR, message VARCHAR, raw VARCHAR)"
                ).format(sql.Identifier(schemas.raw, "openstack"))
            )
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} (line_no, source_file, log_file, ts, pid, level, component, message) "
                    "VALUES (1, 'lab.log', 'nova-compute.log', %s, 1, 'INFO', 'nova.compute.manager', "
                    "'lab line')"
                ).format(sql.Identifier(schemas.raw, "openstack")),
                [datetime(2017, 5, 16, 0, 0, 0)],
            )
        assert derive.main(["--schema", schemas.slm, "--raw-schema", schemas.raw]) == 0
        yield schemas
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            for schema in (schemas.slm, schemas.raw):
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _run_incident_step(database_url: str, lab: Lab) -> None:
    """Run sql/derive/30_incidents.sql alone, the way `make derive` runs it."""
    values = derive.variables(lab.slm, lab.raw, (26, 9, 1))
    with psycopg.connect(database_url, autocommit=True) as conn:
        for statement in derive.split_statements(derive.render(STEP.read_text(), values)):
            conn.execute(statement)


# ----------------------------------------------------------------- the table


def test_the_incident_table_has_exactly_the_registered_columns(database_url: str, lab: Lab) -> None:
    described = _fetch(database_url, sql.SQL("DESCRIBE {}").format(sql.Identifier(lab.slm, "incident")))

    assert tuple(str(row[0]) for row in described) == INCIDENT_COLUMNS


def test_the_incident_id_and_the_counts_are_integers(database_url: str, lab: Lab) -> None:
    types = dict(
        (str(row[0]), str(row[1]))
        for row in _fetch(database_url, sql.SQL("DESCRIBE {}").format(sql.Identifier(lab.slm, "incident")))
    )

    # #1: the running sum() is cast, so the id is not a DECIMAL.
    assert types["incident_id"] == "BIGINT"
    assert types["bursts"] == "BIGINT"
    assert types["entries"] == "BIGINT"
    assert types["top_node_share"] == "DOUBLE"


def test_every_keyed_log_entry_belongs_to_exactly_one_incident(database_url: str, lab: Lab) -> None:
    alert_incidents, in_incidents, keyed, unkeyed = _one(
        database_url,
        sql.SQL(
            "SELECT (SELECT count(*) FROM {inc} WHERE " + ALERTS + "), "
            "(SELECT sum(entries) FROM {inc}), "
            "(SELECT count(*) FROM {bgl} WHERE pattern_key IS NOT NULL), "
            "(SELECT count(*) FROM {bgl} WHERE pattern_key IS NULL)"
        ).format(inc=sql.Identifier(lab.slm, "incident"), bgl=sql.Identifier(lab.slm, "bgl_entry")),
    )

    assert alert_incidents == LAB_ALERT_INCIDENTS
    assert in_incidents == keyed
    assert unkeyed == 0


def test_dataset_pattern_and_incident_id_identify_one_incident(database_url: str, lab: Lab) -> None:
    rows, keys = _one(
        database_url,
        sql.SQL("SELECT count(*), count(DISTINCT (dataset, pattern_key, incident_id)) FROM {}").format(
            sql.Identifier(lab.slm, "incident")
        ),
    )

    assert rows == keys == LAB_ALERT_INCIDENTS + 1


def test_every_row_records_the_default_parameters(database_url: str, lab: Lab) -> None:
    assert _fetch(
        database_url,
        sql.SQL("SELECT DISTINCT dataset, gap_s, merge_scope FROM {}").format(
            sql.Identifier(lab.slm, "incident")
        ),
    ) == [("bgl", GAP_S, "all")]


# ----------------------------------------------------------------- chaining


def test_no_two_neighbours_of_an_incident_are_further_apart_than_the_gap(database_url: str, lab: Lab) -> None:
    """An independent recompute: inside an Incident every gap is at most 300 s, and the gap that
    starts the next Incident of the same Pattern is longer than 300 s."""
    inside, between = _one(
        database_url,
        sql.SQL(
            "SELECT max(gap) FILTER (WHERE same), min(gap) FILTER (WHERE NOT same) FROM ("
            "SELECT epoch_utc - lag(epoch_utc) OVER w AS gap, "
            "incident_id = lag(incident_id) OVER w AS same FROM {bgl} e "
            "JOIN {inc} i ON i.pattern_key = e.pattern_key "
            "AND e.epoch_utc BETWEEN i.t0_utc AND i.t1_utc "
            "WINDOW w AS (PARTITION BY e.pattern_key ORDER BY e.epoch_utc, e.entry_id)) x"
        ).format(inc=sql.Identifier(lab.slm, "incident"), bgl=sql.Identifier(lab.slm, "bgl_entry")),
    )

    assert inside is not None
    assert inside <= GAP_S
    assert between is not None
    assert between > GAP_S


def test_the_fall_back_hour_splits_the_incident_because_the_gap_is_utc(database_url: str, lab: Lab) -> None:
    # LABDST's two Log Entries carry one local time and two UTC seconds an hour apart. On `ts` the
    # gap is 0 s and they would be one Incident; the step must see 3,600 s and make two.
    incidents, local_times, utc_span = _one(
        database_url,
        sql.SQL(
            "SELECT (SELECT count(*) FROM {inc} WHERE pattern_key = 'LABDST'), "
            "(SELECT count(DISTINCT ts) FROM {bgl} WHERE pattern_key = 'LABDST'), "
            "(SELECT max(epoch_utc) - min(epoch_utc) FROM {bgl} WHERE pattern_key = 'LABDST')"
        ).format(inc=sql.Identifier(lab.slm, "incident"), bgl=sql.Identifier(lab.slm, "bgl_entry")),
    )

    assert (local_times, utc_span) == (1, 3600)
    assert incidents == 2


def test_a_gap_longer_than_the_threshold_starts_a_new_incident(database_url: str, lab: Lab) -> None:
    assert _fetch(
        database_url,
        sql.SQL(
            "SELECT incident_id, entries, shape FROM {} WHERE pattern_key = 'LABGAP' ORDER BY incident_id"
        ).format(sql.Identifier(lab.slm, "incident")),
    ) == [(1, 1, "single-node isolated"), (2, 1, "single-node isolated")]


def test_the_incident_bounds_are_the_first_and_last_log_entry(database_url: str, lab: Lab) -> None:
    t0, t1, t0_utc, t1_utc = _one(
        database_url,
        sql.SQL("SELECT t0, t1, t0_utc, t1_utc FROM {} WHERE pattern_key = 'LABSTORM'").format(
            sql.Identifier(lab.slm, "incident")
        ),
    )

    assert t1_utc - t0_utc == 119 * 10
    assert t1 - t0 == timedelta(seconds=119 * 10)
    assert t0_utc == BASE_EPOCH


# ----------------------------------------------------------------- Bursts, Spread and Shape


def test_a_burst_is_the_chain_on_one_source(database_url: str, lab: Lab) -> None:
    # LABBURST: merged across nodes it is one Incident, but PAIR_A's own gap is 400 s, so PAIR_A
    # holds two Bursts and PAIR_B one.
    entries, bursts, nodes, top_node, share = _one(
        database_url,
        sql.SQL(
            "SELECT entries, bursts, nodes, top_node, top_node_share FROM {} WHERE pattern_key = 'LABBURST'"
        ).format(sql.Identifier(lab.slm, "incident")),
    )

    assert (entries, bursts, nodes) == (3, 3, 2)
    assert top_node == PAIR_A
    assert share == pytest.approx(2 / 3)


def test_the_spread_vector_counts_distinct_topology_levels(database_url: str, lab: Lab) -> None:
    assert _fetch(
        database_url,
        sql.SQL(
            "SELECT pattern_key, nodes, nodecards, midplanes, racks FROM {} "
            "WHERE pattern_key IN ('LABSTORM', 'LABBURST', 'LABMID', 'LABWIDE', 'LABNOTOPO') "
            "ORDER BY pattern_key"
        ).format(sql.Identifier(lab.slm, "incident")),
    ) == [
        ("LABBURST", 2, 2, 1, 1),
        ("LABMID", 2, 2, 2, 1),
        # Node names without topology leave nodecard, midplane and rack NULL.
        ("LABNOTOPO", 2, 0, 0, 0),
        ("LABSTORM", 1, 1, 1, 1),
        ("LABWIDE", 64, 64, 64, 32),
    ]


def test_the_spread_vector_matches_an_independent_count(database_url: str, lab: Lab) -> None:
    """Recount the Spread straight from the Log Entries of every Incident."""
    disagreements = _fetch(
        database_url,
        sql.SQL(
            "SELECT i.pattern_key, i.incident_id FROM {inc} i JOIN ("
            "SELECT pattern_key, count(DISTINCT node) AS nodes, count(DISTINCT nodecard) AS nodecards, "
            "count(DISTINCT midplane) AS midplanes, count(DISTINCT rack) AS racks, count(*) AS entries "
            "FROM {bgl} WHERE pattern_key IS NOT NULL GROUP BY pattern_key) e "
            "ON e.pattern_key = i.pattern_key "
            "WHERE (SELECT count(*) FROM {inc} j WHERE j.pattern_key = i.pattern_key) = 1 "
            "AND (i.nodes, i.nodecards, i.midplanes, i.racks, i.entries) "
            "IS DISTINCT FROM (e.nodes, e.nodecards, e.midplanes, e.racks, e.entries)"
        ).format(inc=sql.Identifier(lab.slm, "incident"), bgl=sql.Identifier(lab.slm, "bgl_entry")),
    )

    assert disagreements == []


def test_every_shape_label_is_one_of_the_glossary_labels(database_url: str, lab: Lab) -> None:
    labels = {
        row[0]
        for row in _fetch(
            database_url,
            sql.SQL("SELECT DISTINCT shape FROM {}").format(sql.Identifier(lab.slm, "incident")),
        )
    }

    assert labels <= set(SHAPES)


def test_the_shape_separates_a_single_node_storm_from_a_machine_wide_incident(
    database_url: str, lab: Lab
) -> None:
    assert _fetch(
        database_url,
        sql.SQL(
            "SELECT pattern_key, entries, nodes, midplanes, shape FROM {inc} WHERE " + ALERTS + " "
            "ORDER BY shape, pattern_key"
        ).format(inc=sql.Identifier(lab.slm, "incident"), bgl=sql.Identifier(lab.slm, "bgl_entry")),
    ) == [
        ("LABWIDE", 64, 64, 64, "machine-wide"),
        ("LABMID", 2, 2, 2, "multi-midplane"),
        ("LABBURST", 3, 2, 1, "one midplane"),
        # 0 midplanes falls through to "one midplane". LABNOTOPO is the only case that reaches it,
        # and it only does so because the lab gives it two node names without topology. On the full
        # Base Corpus every such Incident has one node name and is labelled single-node, so the
        # fall-through never claims an unknown Spread (pinned in
        # tests/corpus/test_incidents_corpus.py), and no sixth Shape label is needed.
        ("LABNOTOPO", 2, 2, 0, "one midplane"),
        # The fall-back hour splits LABDST into two, because the gap is taken on epoch_utc.
        ("LABDST", 1, 1, 1, "single-node isolated"),
        ("LABDST", 1, 1, 1, "single-node isolated"),
        ("LABGAP", 1, 1, 1, "single-node isolated"),
        ("LABGAP", 1, 1, 1, "single-node isolated"),
        ("LABISO", 1, 1, 1, "single-node isolated"),
        ("LABSTORM", 120, 1, 1, "single-node storm"),
    ]


# ----------------------------------------------------------------- re-runnable, and ready for T9


def test_a_second_derive_gives_an_identical_incident_table(database_url: str, lab: Lab) -> None:
    before = _fetch(
        database_url,
        sql.SQL("SELECT * FROM {} ORDER BY pattern_key, incident_id").format(
            sql.Identifier(lab.slm, "incident")
        ),
    )

    assert derive.main(["--schema", lab.slm, "--raw-schema", lab.raw]) == 0

    after = _fetch(
        database_url,
        sql.SQL("SELECT * FROM {} ORDER BY pattern_key, incident_id").format(
            sql.Identifier(lab.slm, "incident")
        ),
    )
    assert after == before


def test_running_the_step_again_gives_an_identical_incident_table(database_url: str, lab: Lab) -> None:
    before = _fetch(
        database_url,
        sql.SQL("SELECT * FROM {} ORDER BY pattern_key, incident_id").format(
            sql.Identifier(lab.slm, "incident")
        ),
    )

    _run_incident_step(database_url, lab)

    after = _fetch(
        database_url,
        sql.SQL("SELECT * FROM {} ORDER BY pattern_key, incident_id").format(
            sql.Identifier(lab.slm, "incident")
        ),
    )
    assert after == before


def test_the_pattern_key_t9_fills_adds_incidents_without_a_code_change(database_url: str, lab: Lab) -> None:
    """Acceptance criterion 4, for real now that T9 has merged.

    T9's Pattern dictionary gives the five lab Log Entries that have no alert category a template
    id and sets `pattern_key` to it. The step reads `pattern_key` and nothing else, so the same
    SQL, unchanged, chains them into one more Incident: 5 Log Entries 10 s apart on one node. The
    Incidents of the alert categories are untouched.
    """
    alert_rows = _fetch(
        database_url,
        sql.SQL(
            "SELECT pattern_key, incident_id, entries FROM {inc} WHERE " + ALERTS + " ORDER BY 1, 2"
        ).format(inc=sql.Identifier(lab.slm, "incident"), bgl=sql.Identifier(lab.slm, "bgl_entry")),
    )
    template_rows = _fetch(
        database_url,
        sql.SQL(
            "SELECT i.incident_id, i.entries, i.nodes, i.shape, i.pattern_key = e.template_id "
            "FROM {inc} i JOIN {bgl} e ON e.pattern_key = i.pattern_key "
            "WHERE NOT (i." + ALERTS + ") GROUP BY ALL"
        ).format(inc=sql.Identifier(lab.slm, "incident"), bgl=sql.Identifier(lab.slm, "bgl_entry")),
    )

    assert len(alert_rows) == LAB_ALERT_INCIDENTS
    # One Incident, keyed by the template id T9 assigned, holding every non-alert Log Entry.
    assert template_rows == [(1, LAB_NON_ALERT_ENTRIES, 1, "single-node isolated", True)]


def test_the_step_never_reads_the_alert_category_itself(database_url: str, lab: Lab) -> None:
    """The step must not know about alert categories, or acceptance criterion 4 would need a code
    change for every new Pattern source. Setting `pattern_key` to something that is neither an
    alert category nor a template id must still produce Incidents."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("UPDATE {} SET pattern_key = 'not-a-category' WHERE alert_tag = 'LABISO'").format(
                sql.Identifier(lab.slm, "bgl_entry")
            )
        )
    try:
        _run_incident_step(database_url, lab)

        assert _fetch(
            database_url,
            sql.SQL("SELECT incident_id, entries, nodes, shape FROM {} WHERE pattern_key = %s").format(
                sql.Identifier(lab.slm, "incident")
            ),
            ["not-a-category"],
        ) == [(1, 1, 1, "single-node isolated")]
    finally:
        # Put the lab back, so the module's other tests see the derived state.
        assert derive.main(["--schema", lab.slm, "--raw-schema", lab.raw]) == 0
