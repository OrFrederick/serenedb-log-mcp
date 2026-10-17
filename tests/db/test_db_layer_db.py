"""The database layer against a real SereneDB, in a throwaway schema `scratch_t2_<hex>`.

It never writes to `public`, which holds the shared Base Corpus. The scratch table has 30 Log
Entry-like rows: every second one says "ciod failed to read message", every third one is FATAL, and
they are one hour apart from 2005-06-03 01:00.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from serenedb_log_mcp import db
from serenedb_log_mcp.config import Role
from serenedb_log_mcp.db import Database, QueryFailed, QueryTimeout

pytestmark = pytest.mark.db

ROWS = 30


def _dictionary_ddl(conn: psycopg.Connection[tuple[object, ...]], name: sql.Composable) -> sql.Composed:
    """The text dictionary DDL for this server: `AS split_text(...)` exists only from 26.10.1."""
    row = conn.execute("SELECT version()").fetchone()
    match = re.search(r"SereneDB (\d+)\.(\d+)", str(row[0] if row else ""))
    if match and (int(match[1]), int(match[2])) >= (26, 10):
        return sql.SQL(
            "CREATE TEXT SEARCH DICTIONARY {} AS split_text(case := 'lower') WITH (frequency, position)"
        ).format(name)
    # The legacy form OrFrederick's Claude ran on 26.09.1.3 (#2, c1_index.py).
    return sql.SQL(
        "CREATE TEXT SEARCH DICTIONARY {} (TEMPLATE='text', locale='en_US.UTF-8', \"case\"='lower', "
        "stemming=false, accent=false, frequency=true, position=true)"
    ).format(name)


@pytest.fixture(scope="module")
def scratch(database_url: str) -> Iterator[str]:
    schema = f"scratch_t2_{uuid.uuid4().hex[:8]}"
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL(
                    "CREATE TABLE {} (id BIGINT, ts TIMESTAMP, level TEXT, message TEXT, a BIGINT)"
                ).format(sql.Identifier(schema, "entry"))
            )
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} SELECT i, TIMESTAMP '2005-06-03 00:00:00' + i * INTERVAL '1 hour', "
                    "CASE WHEN i % 3 = 0 THEN 'FATAL' ELSE 'INFO' END, "
                    "CASE WHEN i % 2 = 0 THEN 'ciod failed to read message' "
                    "ELSE 'kernel generated core' END, "
                    "i FROM generate_series(1, {}) g(i)"
                ).format(sql.Identifier(schema, "entry"), sql.Literal(ROWS))
            )
            conn.execute(_dictionary_ddl(conn, sql.Identifier(schema, "words")))
            conn.execute(
                sql.SQL("CREATE INDEX entry_idx ON {} USING inverted (id, message {}, ts, level)").format(
                    sql.Identifier(schema, "entry"), sql.Identifier(schema, "words")
                )
            )
            conn.execute(sql.SQL("VACUUM (REFRESH_TABLE) {}").format(sql.Identifier(schema, "entry")))
        yield schema
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def layer(database_url: str) -> Iterator[Database]:
    database = Database(database_url, timeout_s=15)
    yield database
    database.close()


def scratch_count(database_url: str, schema: str) -> int:
    with psycopg.connect(database_url, autocommit=True) as conn:
        query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, "entry"))
        row = conn.execute(query).fetchone()
    assert row is not None
    return int(row[0])


def tables_in(database_url: str, schema: str) -> set[str]:
    with psycopg.connect(database_url, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", [schema]
        ).fetchall()
    return {str(row[0]) for row in rows}


# Read-only ------------------------------------------------------------------------------------------


def test_insert_through_the_layer_fails_and_writes_nothing(
    layer: Database, scratch: str, database_url: str
) -> None:
    with pytest.raises(QueryFailed) as caught:
        layer.fetch_all(f"INSERT INTO {scratch}.entry VALUES (99, TIMESTAMP '2005-06-03', 'INFO', 'x', 99)")

    assert caught.value.sqlstate == "25006"
    assert scratch_count(database_url, scratch) == ROWS


def test_create_through_the_layer_fails(layer: Database, scratch: str, database_url: str) -> None:
    with pytest.raises(QueryFailed) as caught:
        layer.fetch_all(f"CREATE TABLE {scratch}.made_by_the_layer (a INT)")

    assert caught.value.sqlstate == "25006"
    assert "made_by_the_layer" not in tables_in(database_url, scratch)


def test_full_text_query_succeeds(layer: Database, scratch: str) -> None:
    where = f"FROM {scratch}.entry_idx WHERE message @@ websearch_to_tsquery(%s)"

    result = layer.fetch_limited(
        f"SELECT count(*) {where}", f"SELECT id, ts, message {where} ORDER BY ts LIMIT %s", ["ciod failed"], 5
    )

    assert result.total == ROWS // 2
    assert [row[0] for row in result.rows] == [2, 4, 6, 8, 10]
    assert result.truncated is True
    assert result.params == [["ciod failed"], ["ciod failed", 5]]


def test_group_by_succeeds(layer: Database, scratch: str) -> None:
    result = layer.fetch_all(f"SELECT level, count(*) FROM {scratch}.entry GROUP BY level ORDER BY level")

    assert result.rows == [("FATAL", 10), ("INFO", 20)]


def test_time_bucket_succeeds(layer: Database, scratch: str) -> None:
    result = layer.fetch_all(
        f"SELECT time_bucket(INTERVAL '1 day', ts) AS day, count(*) FROM {scratch}.entry "
        "GROUP BY 1 ORDER BY 1"
    )

    assert [count for _, count in result.rows] == [23, 7]


# Numeric results --------------------------------------------------------------------------------------


def test_numeric_results_come_back_as_int_or_float(layer: Database, scratch: str) -> None:
    result = layer.fetch_all(
        "SELECT sum(a), CAST(sum(a) AS NUMERIC(20, 2)) / 4, 12345678901234567890::HUGEINT "
        f"FROM {scratch}.entry"
    )
    window = layer.fetch_all(f"SELECT sum(a) OVER () FROM {scratch}.entry ORDER BY id LIMIT 1")

    total, quarter, huge = result.rows[0]
    assert (total, type(total)) == (465, int)
    assert (quarter, type(quarter)) == (116.25, float)
    assert (huge, type(huge)) == (12345678901234567890, int)
    assert window.rows == [(465,)]
    assert type(window.rows[0][0]) is int


def test_binary_numeric_results_come_back_as_int_or_float(database_url: str) -> None:
    """The layer reads text format, but a connection from open_connection converts binary too."""
    with db.open_connection(database_url, client_binding=False) as conn:
        row = conn.execute(
            "SELECT 5050::numeric, 1.5::numeric, sum(i), 12345678901234567890::HUGEINT "
            "FROM generate_series(1, 100) g(i)",
            binary=True,
        ).fetchone()

    assert row is not None
    assert row == (5050, 1.5, 5050, 12345678901234567890)
    assert [type(value) for value in row] == [int, float, int, int]


# Timeout ----------------------------------------------------------------------------------------------


def test_a_slow_query_is_cancelled_and_the_next_query_succeeds(database_url: str) -> None:
    layer = Database(database_url, timeout_s=0.5)
    try:
        # BEGIN, the statement and ROLLBACK cost three round trips; the tunnel to the shared dev
        # instance makes those slow, so the bound is "2 s more than a trivial call".
        started = time.monotonic()
        layer.fetch_all("SELECT 1")
        trivial = time.monotonic() - started

        started = time.monotonic()
        with pytest.raises(QueryTimeout):
            layer.fetch_all("SELECT sleep_ms(30000)")  # sleep_ms exists on 26.09.1.3 and 26.10.1
        elapsed = time.monotonic() - started

        assert elapsed < trivial + 2.0
        assert layer.fetch_all("SELECT 1").rows == [(1,)]
    finally:
        layer.close()


def test_a_bound_slow_query_is_never_returned_silently(database_url: str) -> None:
    """26.09.1.3 ignores a cancel when a table function takes a bound parameter; 26.10.1 honours it.

    Either way the call must not look healthy: it raises QueryTimeout, or its result says that the
    statements ran past the limit.
    """
    layer = Database(database_url, timeout_s=0.5)
    try:
        try:
            result = layer.fetch_all("SELECT sum(i * i) FROM generate_series(1, %s) g(i)", [1_000_000_000])
        except QueryTimeout:
            return
        assert any("past the 0.5 s limit" in note for note in result.notes), result.notes
    finally:
        layer.close()


# Statement errors -------------------------------------------------------------------------------------


def test_an_io_error_is_a_failed_statement_not_a_restart(layer: Database) -> None:
    """SereneDB reports a missing read_csv file as SQLSTATE 08000 on a healthy connection."""
    with pytest.raises(QueryFailed) as caught:
        layer.fetch_all("SELECT * FROM read_csv('/nonexistent/scratch_t2/missing.csv')")

    assert caught.value.sqlstate == "08000"
    assert "missing.csv" in str(caught.value)
    assert layer.fetch_all("SELECT 1").notes == []


# Binding ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("client_binding", [False, True], ids=["server-binding", "client-cursor"])
def test_an_injection_string_passed_as_a_value_matches_nothing(
    client_binding: bool, scratch: str, database_url: str
) -> None:
    layer = Database(database_url, client_binding=client_binding)
    where = f"FROM {scratch}.entry WHERE level = %s"
    try:
        for value in ["INFO' OR '1'='1", f"x'; DROP TABLE {scratch}.entry; --"]:
            count_sql, rows_sql = f"SELECT count(*) {where}", f"SELECT id {where} LIMIT %s"
            result = layer.fetch_limited(count_sql, rows_sql, [value], 5)

            assert result.total == 0
            assert result.rows == []
    finally:
        layer.close()

    assert scratch_count(database_url, scratch) == ROWS


@pytest.mark.parametrize(
    ("client_binding", "cursor_type"), [(False, psycopg.Cursor), (True, psycopg.ClientCursor)]
)
def test_slm_client_binding_selects_the_cursor(
    client_binding: bool, cursor_type: type[psycopg.Cursor[tuple[object, ...]]], database_url: str
) -> None:
    with db.open_connection(database_url, client_binding=client_binding) as conn:
        assert type(conn.cursor()) is cursor_type


def test_the_module_helpers_use_the_layer_for_the_role() -> None:
    result = db.fetch_all("SELECT 1", role=Role.ENGINEER)

    assert result.rows == [(1,)]
    assert result.sql == ["SELECT 1"]
