"""The derive step against a real SereneDB, on the Loghub 2k samples in throwaway schemas."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
import pytest
from psycopg import sql

import derive
from serenedb_log_mcp.corpus.datasets import OWNERSHIP_IS_STUB, OWNERSHIP_NOTE, OWNERSHIP_STUB_SOURCE

CANONICAL_TABLES = ("bgl_entry", "os_entry", "ownership")
SYNTHETIC_BGL_ROWS = 11  # tests/db/conftest.py adds them to the 2,000-line BGL sample


class Schemas(Protocol):
    raw: str
    slm: str


@dataclass(frozen=True)
class SimpleSchemas:
    raw: str
    slm: str


def rows(
    database_url: str, query: sql.Composable | str, params: list[Any] | None = None
) -> list[tuple[Any, ...]]:
    with psycopg.connect(database_url, autocommit=True) as conn:
        return [tuple(r) for r in conn.execute(query, params).fetchall()]


def one(database_url: str, query: sql.Composable | str, params: list[Any] | None = None) -> tuple[Any, ...]:
    result = rows(database_url, query, params)
    assert len(result) == 1
    return result[0]


def q(template: str, schemas: Schemas, **tables: str) -> sql.Composed:
    """Format `template` with {name} placeholders for slm/raw tables, for example bgl='slm.bgl_entry'."""
    names = {}
    for key, ref in tables.items():
        schema, table = ref.split(".")
        names[key] = sql.Identifier(getattr(schemas, schema), table)
    return sql.SQL(template).format(**names)


def counts(database_url: str, schemas: Schemas) -> dict[str, int]:
    return {
        table: one(database_url, q("SELECT count(*) FROM {t}", schemas, t=f"slm.{table}"))[0]
        for table in CANONICAL_TABLES
    }


# ----------------------------------------------------------------- row counts and re-runs


def test_every_raw_line_becomes_one_canonical_log_entry(database_url: str, derived_sample: Schemas) -> None:
    for raw, canonical in (("bgl", "bgl_entry"), ("openstack", "os_entry")):
        raw_count, raw_ids = one(
            database_url,
            q("SELECT count(*), count(DISTINCT line_no) FROM {t}", derived_sample, t=f"raw.{raw}"),
        )
        entries, ids = one(
            database_url,
            q("SELECT count(*), count(DISTINCT entry_id) FROM {t}", derived_sample, t=f"slm.{canonical}"),
        )
        assert entries == raw_count == ids == raw_ids


def test_the_bgl_sample_has_2000_lines_plus_the_synthetic_ones(
    database_url: str, derived_sample: Schemas
) -> None:
    assert counts(database_url, derived_sample)["bgl_entry"] == 2000 + SYNTHETIC_BGL_ROWS
    assert counts(database_url, derived_sample)["os_entry"] == 2000


def test_a_second_derive_gives_identical_row_counts(database_url: str, derived_sample: Schemas) -> None:
    before = counts(database_url, derived_sample)

    assert derive.main(["--schema", derived_sample.slm, "--raw-schema", derived_sample.raw]) == 0

    assert counts(database_url, derived_sample) == before
    # The indexes were rebuilt and answer again.
    (matches,) = one(
        database_url,
        q("SELECT count(*) FROM {i} WHERE message @@ 'instruction'", derived_sample, i="slm.bgl_entry_idx"),
    )
    assert matches > 0


def test_derive_prints_the_topology_report(
    derived_sample: Schemas, capsys: pytest.CaptureFixture[str]
) -> None:
    assert derive.main(["--schema", derived_sample.slm, "--raw-schema", derived_sample.raw]) == 0

    out = capsys.readouterr().out
    assert re.search(r"bgl_entry: \d[\d,]* Log Entries have a node name outside Rxx-My-Nz", out)
    assert "UNKNOWN_LOCATION" in out
    assert "Components without a Team: none" in out


# ----------------------------------------------------------------- BGL derived columns


@pytest.mark.parametrize(
    ("node", "rack", "midplane", "nodecard"),
    [
        ("R30-M0-N9-C:J16-U01", "R30", "R30-M0", "R30-M0-N9"),
        ("R71-M0-NF-C:J02-U01", "R71", "R71-M0", "R71-M0-NF"),
        ("R63-M0-N0", "R63", "R63-M0", "R63-M0-N0"),
        ("R27-M1-L3-U18-C", "R27", "R27-M1", None),
        ("R63-M0", "R63", "R63-M0", None),
        ("R76", "R76", None, None),
        ("NULL", None, None, None),
        ("UNKNOWN_LOCATION", None, None, None),
    ],
)
def test_topology_is_parsed_from_the_node_name(
    database_url: str,
    derived_sample: Schemas,
    node: str,
    rack: str | None,
    midplane: str | None,
    nodecard: str | None,
) -> None:
    result = rows(
        database_url,
        q(
            "SELECT DISTINCT rack, midplane, nodecard FROM {t} WHERE node = %s",
            derived_sample,
            t="slm.bgl_entry",
        ),
        [node],
    )

    assert result == [(rack, midplane, nodecard)]


def test_every_sample_node_card_is_a_prefix_of_its_node(database_url: str, derived_sample: Schemas) -> None:
    (bad,) = one(
        database_url,
        q(
            "SELECT count(*) FROM {t} WHERE (nodecard IS NOT NULL AND NOT starts_with(node, nodecard)) "
            "OR (midplane IS NOT NULL AND NOT starts_with(nodecard, midplane) AND nodecard IS NOT NULL) "
            "OR (rack IS NOT NULL AND NOT starts_with(midplane, rack))",
            derived_sample,
            t="slm.bgl_entry",
        ),
    )
    assert bad == 0


def test_epoch_utc_is_the_utc_second_plus_the_logged_fraction(
    database_url: str, derived_sample: Schemas
) -> None:
    (bad,) = one(
        database_url,
        q(
            "SELECT count(*) FROM {e} e JOIN {r} r ON e.entry_id = r.line_no "
            "WHERE abs(e.epoch_utc - (r.epoch + (epoch_us(r.ts) % 1000000) / 1e6)) > 1e-6 "
            "OR e.epoch_utc IS NULL",
            derived_sample,
            e="slm.bgl_entry",
            r="raw.bgl",
        ),
    )
    assert bad == 0


def test_epoch_utc_follows_us_pacific_daylight_saving_time(
    database_url: str, derived_sample: Schemas
) -> None:
    offsets = dict(
        rows(
            database_url,
            q(
                "SELECT entry_id, round(epoch_utc - epoch_us(ts) / 1e6)::BIGINT FROM {t} "
                "WHERE entry_id IN (900001, 900007, 900008, 900009)",
                derived_sample,
                t="slm.bgl_entry",
            ),
        )
    )

    assert offsets == {900_001: 25_200, 900_007: 25_200, 900_008: 28_800, 900_009: 28_800}


def test_the_fall_back_hour_keeps_its_real_gap(database_url: str, derived_sample: Schemas) -> None:
    # Same local time, one hour apart in UTC: gaps must be computed on epoch_utc, never on ts.
    (ts_gap, utc_gap) = one(
        database_url,
        q(
            "SELECT max(epoch_us(ts)) - min(epoch_us(ts)), max(epoch_utc) - min(epoch_utc) FROM {t} "
            "WHERE entry_id IN (900007, 900008)",
            derived_sample,
            t="slm.bgl_entry",
        ),
    )

    assert ts_gap == 0
    assert utc_gap == pytest.approx(3600)


def test_alert_lines_set_alert_tag_and_pattern_key(database_url: str, derived_sample: Schemas) -> None:
    result = one(
        database_url,
        q(
            "SELECT count(*) FILTER (WHERE r.alert_tag <> '-' AND e.alert_tag = r.alert_tag "
            "AND e.pattern_key = r.alert_tag), count(*) FILTER (WHERE r.alert_tag <> '-'), "
            "count(*) FILTER (WHERE r.alert_tag = '-' AND e.alert_tag IS NULL "
            "AND e.pattern_key = e.template_id), "
            "count(*) FILTER (WHERE r.alert_tag = '-'), count(e.template_id), count(e.family_id), count(*) "
            "FROM {e} e JOIN {r} r ON e.entry_id = r.line_no",
            derived_sample,
            e="slm.bgl_entry",
            r="raw.bgl",
        ),
    )
    alert_ok, alerts, plain_ok, plain, template_ids, family_ids, total = result

    assert alerts > 0
    assert alert_ok == alerts
    # Non-alert lines are keyed by their Pattern once the Pattern step (T9) has run.
    assert plain_ok == plain
    assert template_ids == family_ids == total


# ----------------------------------------------------------------- OpenStack derived columns

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
REQUEST_RE = re.compile(r'"([A-Z]+) ([^ ?"]+)[^"]*" status: (\d+) len: \d+ time: ([0-9.]+)')
INSTANCE_RES = (
    re.compile(rf"\[instance: ({UUID})\]"),
    re.compile(rf"/servers/({UUID})"),
    re.compile(rf"for instance ({UUID})"),
)


def expected_endpoint(method: str, path: str) -> str:
    path = re.sub(UUID, "{uuid}", path)
    path = re.sub(r"[0-9a-f]{32}", "{tenant}", path)
    path = re.sub(r"/\d+(?=/|$)", "/{n}", path)
    return f"{method} {path}"


def expected_instance(message: str) -> str | None:
    for pattern in INSTANCE_RES:
        if match := pattern.search(message):
            return match.group(1)
    return None


def test_openstack_columns_match_an_independent_parse(database_url: str, derived_sample: Schemas) -> None:
    result = rows(
        database_url,
        q(
            "SELECT message, endpoint, http_status, latency_s, instance_ref, "
            "template_id, family_id, pattern_key FROM {t}",
            derived_sample,
            t="slm.os_entry",
        ),
    )
    with_status = 0
    for message, endpoint, status, latency, instance, template_id, family_id, pattern_key in result:
        match = REQUEST_RE.search(message or "")
        if match:
            with_status += 1
            assert endpoint == expected_endpoint(match.group(1), match.group(2)), message
            assert status == int(match.group(3)), message
            assert latency == pytest.approx(float(match.group(4))), message
        else:
            assert (endpoint, status, latency) == (None, None, None), message
        assert instance == expected_instance(message or ""), message
        # The Pattern step (T9) gives every Log Entry a Pattern, which is its pattern_key.
        assert template_id is not None, message
        assert family_id is not None, message
        assert pattern_key == template_id, message
    assert with_status > 0
    assert any(r[4] is not None for r in result)


def test_openstack_sources_get_neutral_ids_in_load_order(database_url: str, derived_sample: Schemas) -> None:
    result = rows(
        database_url,
        q(
            "SELECT source_id, count(*), min(entry_id), max(entry_id) FROM {t} GROUP BY 1 ORDER BY 3",
            derived_sample,
            t="slm.os_entry",
        ),
    )

    assert result == [("os-1", 700, 1, 700), ("os-2", 700, 701, 1400), ("os-3", 600, 1401, 2000)]


# ----------------------------------------------------------------- ground-truth hygiene


def relations(database_url: str, schema: str) -> list[str]:
    return [
        name
        for (name,) in rows(
            database_url,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY 1",
            [schema],
        )
    ]


def text_columns(database_url: str, schema: str) -> list[tuple[str, str]]:
    """(relation, column) for every text column of every table and view in `schema`."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        described = derive.relation_columns(conn, schema)
    return [
        (relation, column)
        for relation, columns in described.items()
        for column, kind in columns
        if kind.upper() in {"VARCHAR", "TEXT"}
    ]


GENUINE_PATTERN_TEXT = {
    ("pattern_dict", "message"),
    ("pattern_dict", "template_text"),
    ("pattern", "template_text"),
    ("pattern", "display_text"),
}


def test_no_text_column_of_any_canonical_relation_contains_abnormal(
    database_url: str, derived_sample: Schemas
) -> None:
    columns = text_columns(database_url, derived_sample.slm)
    assert ("os_entry", "source_id") in columns
    assert ("bgl_entry", "message") in columns
    hits = {}
    for table, column in columns:
        # BGL's own log text says "exited abnormally" (the MASABNORM alert lines); that is a Log
        # Entry, not a label, and the BGL Patterns (T9) carry the same text. Every other match
        # would be a leak of the OpenStack labels.
        exempt = ""
        if (table, column) == ("bgl_entry", "message"):
            exempt = "AND lower({c}) NOT LIKE '%exited abnormally%'"
        elif (table, column) in GENUINE_PATTERN_TEXT:
            exempt = "AND NOT (dataset = 'bgl' AND lower({c}) LIKE '%exited abnormally%')"
        query = sql.SQL("SELECT count(*) FROM {t} WHERE lower({c}) LIKE '%abnormal%' " + exempt).format(
            t=sql.Identifier(derived_sample.slm, table), c=sql.Identifier(column)
        )
        (count,) = one(database_url, query)
        if count:
            hits[f"{table}.{column}"] = count

    assert hits == {}


def test_the_genuine_bgl_text_is_kept(database_url: str, derived_sample: Schemas) -> None:
    (count,) = one(
        database_url,
        q(
            "SELECT count(*) FROM {t} WHERE message LIKE '%exited abnormally%'",
            derived_sample,
            t="slm.bgl_entry",
        ),
    )
    assert count == 1


def test_no_label_relation_or_file_name_column_exists(database_url: str, derived_sample: Schemas) -> None:
    # os_instance: the OpenStack instance phases (T11), computed from timing only.
    assert relations(database_url, derived_sample.slm) == [
        "bgl_entry",
        "os_entry",
        "os_instance",
        "ownership",
        "pattern",
        "pattern_dict",
    ]
    names = {column for _, column in text_columns(database_url, derived_sample.slm)}
    assert not names & {"source_file", "raw", "label", "anomaly", "is_anomaly"}


# ----------------------------------------------------------------- Ownership Table


def test_every_component_of_both_datasets_has_a_team(database_url: str, derived_sample: Schemas) -> None:
    unmapped = rows(
        database_url,
        q(
            "SELECT e.dataset, e.component FROM (SELECT DISTINCT 'bgl' AS dataset, component FROM {b} "
            "UNION SELECT DISTINCT 'openstack', component FROM {o}) e "
            "LEFT JOIN {own} w ON w.dataset = e.dataset AND w.component = e.component "
            "WHERE e.component IS NOT NULL AND w.team IS NULL",
            derived_sample,
            b="slm.bgl_entry",
            o="slm.os_entry",
            own="slm.ownership",
        ),
    )
    assert unmapped == []


def test_the_ownership_table_has_one_team_per_component(database_url: str, derived_sample: Schemas) -> None:
    duplicates = rows(
        database_url,
        q(
            "SELECT dataset, component FROM {t} GROUP BY 1, 2 HAVING count(*) > 1",
            derived_sample,
            t="slm.ownership",
        ),
    )
    (teams, datasets) = one(
        database_url,
        q("SELECT count(DISTINCT team), count(DISTINCT dataset) FROM {t}", derived_sample, t="slm.ownership"),
    )

    assert duplicates == []
    assert teams >= 2
    assert datasets == 2


def test_the_ownership_table_has_the_canonical_columns(database_url: str, derived_sample: Schemas) -> None:
    described = rows(database_url, q("DESCRIBE {t}", derived_sample, t="slm.ownership"))

    assert [(str(r[0]), str(r[1])) for r in described] == [
        ("dataset", "VARCHAR"),
        ("component", "VARCHAR"),
        ("team", "VARCHAR"),
        ("team_source", "VARCHAR"),
    ]


def test_every_ownership_row_names_the_source_of_its_team(database_url: str, derived_sample: Schemas) -> None:
    # The provenance travels with every join on the Ownership Table (#48); T25 sets its own value.
    sources = dict(
        rows(
            database_url,
            q("SELECT team_source, count(*) FROM {t} GROUP BY 1", derived_sample, t="slm.ownership"),
        )
    )

    assert None not in sources
    assert sources == {OWNERSHIP_STUB_SOURCE: 22}
    # The Python flag that tools read agrees with the table, so T25 cannot swap one and not the other.
    assert OWNERSHIP_IS_STUB is (set(sources) == {OWNERSHIP_STUB_SOURCE})


def test_the_report_says_the_teams_come_from_the_stub(database_url: str, derived_sample: Schemas) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        lines = derive.report(conn, derived_sample.slm)

    assert "Ownership Table team_source: stub 22" in lines
    assert f"NOTE: {OWNERSHIP_NOTE}" in lines


# ----------------------------------------------------------------- inverted indexes


def index_count(database_url: str, schemas: Schemas, index: str, predicate: str, params: list[Any]) -> int:
    (count,) = one(
        database_url, q("SELECT count(*) FROM {i} WHERE " + predicate, schemas, i=f"slm.{index}"), params
    )
    return int(count)


def table_count(database_url: str, schemas: Schemas, table: str, predicate: str, params: list[Any]) -> int:
    (count,) = one(
        database_url, q("SELECT count(*) FROM {t} WHERE " + predicate, schemas, t=f"slm.{table}"), params
    )
    return int(count)


def test_full_text_matches_a_regex_scan(database_url: str, derived_sample: Schemas) -> None:
    indexed = index_count(database_url, derived_sample, "bgl_entry_idx", "message @@ %s", ["instruction"])
    scanned = table_count(
        database_url, derived_sample, "bgl_entry", "regexp_matches(lower(message), %s)", [r"\binstruction\b"]
    )

    assert indexed == scanned > 0


def test_phrase_search_and_highlighting_work(database_url: str, derived_sample: Schemas) -> None:
    result = rows(
        database_url,
        q(
            "SELECT ts_highlight(i.message) FROM {i} AS i WHERE i.message @@ ts_phrase(%s) LIMIT 3",
            derived_sample,
            i="slm.bgl_entry_idx",
        ),
        ["parity error"],
    )

    assert result
    assert all("<b>parity error</b>" in h for (h,) in result)


def test_bm25_scores_rank_matches(database_url: str, derived_sample: Schemas) -> None:
    result = rows(
        database_url,
        q(
            "SELECT BM25(i.tableoid) AS score FROM {i} AS i WHERE i.message @@ %s "
            "ORDER BY score DESC LIMIT 5",
            derived_sample,
            i="slm.os_entry_idx",
        ),
        ["instance"],
    )

    assert result
    assert all(score > 0 for (score,) in result)


@pytest.mark.parametrize(
    ("index", "table", "column", "value"),
    [
        ("bgl_entry_idx", "bgl_entry", "rack", "R02"),
        ("bgl_entry_idx", "bgl_entry", "nodecard", "R63-M0-N0"),
        ("bgl_entry_idx", "bgl_entry", "component", "KERNEL"),
        ("bgl_entry_idx", "bgl_entry", "level", "FATAL"),
        ("bgl_entry_idx", "bgl_entry", "alert_tag", "KERNDTLB"),
        ("bgl_entry_idx", "bgl_entry", "pattern_key", "KERNDTLB"),
        ("os_entry_idx", "os_entry", "source_id", "os-2"),
        ("os_entry_idx", "os_entry", "component", "nova.compute.manager"),
        ("os_entry_idx", "os_entry", "endpoint", "GET /v2/{tenant}/servers/detail"),
    ],
)
def test_verbatim_filter_columns_are_indexed(
    database_url: str, derived_sample: Schemas, index: str, table: str, column: str, value: str
) -> None:
    indexed = index_count(database_url, derived_sample, index, f"{column} @@ %s", [value])
    exact = table_count(database_url, derived_sample, table, f"{column} = %s", [value])

    assert indexed == exact > 0


def test_numeric_and_time_columns_answer_range_filters(database_url: str, derived_sample: Schemas) -> None:
    slow = index_count(database_url, derived_sample, "os_entry_idx", "latency_s @@ ts_ge(%s::DOUBLE)", [0.5])
    window = index_count(
        database_url,
        derived_sample,
        "bgl_entry_idx",
        "ts @@ ts_between(%s::TIMESTAMP, %s::TIMESTAMP, true, false)",
        ["2005-06-03 00:00:00", "2005-06-04 00:00:00"],
    )

    assert slow == table_count(database_url, derived_sample, "os_entry", "latency_s >= %s", [0.5]) > 0
    assert window == table_count(
        database_url,
        derived_sample,
        "bgl_entry",
        "ts >= %s::TIMESTAMP AND ts < %s::TIMESTAMP",
        ["2005-06-03 00:00:00", "2005-06-04 00:00:00"],
    )
    assert window > 0


# ----------------------------------------------------------------- the adapter contract


def test_the_dev_adapter_tables_can_be_read_and_match_the_contract(
    database_url: str, derived_sample: Schemas
) -> None:
    # Fails on SereneDB 26.09.1.3 if the adapter ever goes back to temporary views, which that
    # version cannot read (InsufficientPrivilege).
    values = derive.variables(derived_sample.slm, derived_sample.raw, (26, 10, 1))
    with psycopg.connect(database_url, autocommit=True) as conn:
        derive.run(conn, [derive.SQL_DIR / "adapters" / "dev.sql"], values)
        found, reasons = derive.adapter_columns(conn)
        adapted = {
            table: conn.execute(f"SELECT count(*) FROM temp.main.{table}").fetchone()
            for table in derive.ADAPTER_TABLES
        }

    assert reasons == {}
    assert {table: tuple(columns) for table, columns in found.items()} == derive.ADAPTER_TABLES
    raw_counts = {
        "bgl_adapted": one(database_url, q("SELECT count(*) FROM {t}", derived_sample, t="raw.bgl")),
        "os_adapted": one(database_url, q("SELECT count(*) FROM {t}", derived_sample, t="raw.openstack")),
    }
    assert adapted == raw_counts


def test_a_broken_adapter_table_stops_the_derive_step(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "CREATE TEMPORARY TABLE bgl_adapted AS SELECT 1::BIGINT AS entry_id, 'x' AS source_file, "
            "now()::TIMESTAMP AS epoch_utc"
        )
        with pytest.raises(derive.DeriveError) as excinfo:
            derive.check_adapter(conn, "official")

    message = str(excinfo.value)
    assert "sql/adapters/official.sql breaks the adapter contract" in message
    assert "os_adapted is missing" in message
    assert "bgl_adapted lacks message" in message
    assert "bgl_adapted has extra column source_file" in message
    assert "bgl_adapted.epoch_utc is TIMESTAMP, expected DOUBLE" in message


# ----------------------------------------------------------------- the raw tables stay replaceable


def views(database_url: str, schema: str) -> list[str]:
    return [
        name
        for (name,) in rows(
            database_url,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s "
            "AND table_type = 'VIEW' ORDER BY 1",
            [schema],
        )
    ]


def test_no_view_is_left_in_slm_schema_after_a_derive(database_url: str, derived_sample: Schemas) -> None:
    assert views(database_url, derived_sample.slm) == []


@pytest.fixture
def small_raw(database_url: str, derived_sample: Schemas) -> Iterator[Schemas]:
    """A 50-row copy of the sample raw tables and an empty target schema, both dropped afterwards."""
    suffix = uuid.uuid4().hex[:8]
    schemas = SimpleSchemas(raw=f"scratch_t4_{suffix}_raw", slm=f"scratch_t4_{suffix}")
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schemas.raw)))
            for table in ("bgl", "openstack"):
                conn.execute(
                    sql.SQL("CREATE TABLE {} AS SELECT * FROM {} ORDER BY line_no LIMIT 50").format(
                        sql.Identifier(schemas.raw, table), sql.Identifier(derived_sample.raw, table)
                    )
                )
        yield schemas
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            for schema in (schemas.slm, schemas.raw):
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def test_the_raw_tables_can_be_swapped_after_a_derive(database_url: str, small_raw: Schemas) -> None:
    assert derive.main(["--schema", small_raw.slm, "--raw-schema", small_raw.raw]) == 0

    # The dev loader's atomic swap: copy into <table>__load, then DROP and RENAME in one transaction.
    with psycopg.connect(database_url) as conn:
        for table in ("bgl", "openstack"):
            raw, load = sql.Identifier(small_raw.raw, table), sql.Identifier(small_raw.raw, f"{table}__load")
            conn.execute(sql.SQL("CREATE TABLE {} AS SELECT * FROM {}").format(load, raw))
            conn.execute(sql.SQL("DROP TABLE {}").format(raw))
            conn.execute(sql.SQL("ALTER TABLE {} RENAME TO {}").format(load, sql.Identifier(table)))
        conn.commit()

    assert views(database_url, small_raw.slm) == []
    assert derive.main(["--schema", small_raw.slm, "--raw-schema", small_raw.raw]) == 0


def test_a_leftover_persistent_adapter_view_is_dropped(database_url: str, small_raw: Schemas) -> None:
    # Derives before this fix left slm.bgl_adapted and slm.os_adapted behind.
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(small_raw.slm)))
        for view, table in (("bgl_adapted", "bgl"), ("os_adapted", "openstack")):
            conn.execute(
                sql.SQL("CREATE VIEW {} AS SELECT * FROM {}").format(
                    sql.Identifier(small_raw.slm, view), sql.Identifier(small_raw.raw, table)
                )
            )

    assert derive.main(["--schema", small_raw.slm, "--raw-schema", small_raw.raw]) == 0

    assert views(database_url, small_raw.slm) == []


# ----------------------------------------------------------------- failures and the floor check


@pytest.fixture
def scratch_pair(database_url: str) -> Iterator[Schemas]:
    """An empty raw schema and a target schema, both dropped afterwards."""
    suffix = uuid.uuid4().hex[:8]
    schemas = SimpleSchemas(raw=f"scratch_t4_{suffix}_raw", slm=f"scratch_t4_{suffix}")
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schemas.raw)))
    try:
        yield schemas
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            for schema in (schemas.slm, schemas.raw):
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def test_a_missing_raw_table_names_the_adapter_step_and_does_not_advise_a_retry(
    scratch_pair: Schemas, capsys: pytest.CaptureFixture[str]
) -> None:
    assert derive.main(["--schema", scratch_pair.slm, "--raw-schema", scratch_pair.raw]) == 2

    err = capsys.readouterr().err
    assert "adapters/dev.sql" in err
    assert "bgl" in err
    assert "run it again" not in err


def test_empty_raw_tables_fail_the_floor_check(
    database_url: str, derived_sample: Schemas, scratch_pair: Schemas, capsys: pytest.CaptureFixture[str]
) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        for table in ("bgl", "openstack"):
            conn.execute(
                sql.SQL("CREATE TABLE {} AS SELECT * FROM {} LIMIT 0").format(
                    sql.Identifier(scratch_pair.raw, table), sql.Identifier(derived_sample.raw, table)
                )
            )

    assert derive.main(["--schema", scratch_pair.slm, "--raw-schema", scratch_pair.raw]) == 2

    err = capsys.readouterr().err
    assert f"{scratch_pair.slm}.bgl_entry is empty" in err
    assert f"{scratch_pair.slm}.os_entry is empty" in err


def test_the_floor_check_names_a_missing_index(database_url: str, derived_sample: Schemas) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        assert derive.result_problems(conn, derived_sample.slm) == []
        conn.execute(sql.SQL("DROP INDEX {}").format(sql.Identifier(derived_sample.slm, "os_entry_idx")))
        try:
            problems = derive.result_problems(conn, derived_sample.slm)
        finally:
            assert derive.main(["--schema", derived_sample.slm, "--raw-schema", derived_sample.raw]) == 0

    assert problems == [f"index {derived_sample.slm}.os_entry_idx is missing"]
