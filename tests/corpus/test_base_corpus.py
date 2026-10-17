"""Golden facts about the raw Base Corpus as loaded by `make load-dev` (dev loader, Zenodo 8196385).

The per-column counts pin the parser: a shifted BGL line or an unparsed OpenStack line changes them.
T25 re-pins these numbers when the organisers' loader replaces the dev loader. The corpus gate in
tests/conftest.py requires the columns read here.
"""

from datetime import datetime

import psycopg
import pytest
from psycopg import sql

from serenedb_log_mcp.config import load_settings

pytestmark = pytest.mark.corpus

BGL_LEVELS = "('INFO', 'WARNING', 'SEVERE', 'ERROR', 'FATAL', 'FAILURE')"


def one(database_url: str, query: str, table: str) -> tuple[object, ...]:
    schema = load_settings().raw_schema
    with psycopg.connect(database_url, autocommit=True) as conn:
        row = conn.execute(sql.SQL(query).format(sql.Identifier(schema, table))).fetchone()
    assert row is not None
    return tuple(row)


def test_bgl_rows_alert_lines_and_time_range(database_url: str) -> None:
    rows, alerts, first, last = one(
        database_url,
        "SELECT count(*), count(*) FILTER (WHERE alert_tag <> '-'), min(ts), max(ts) FROM {}",
        "bgl",
    )

    assert rows == 4_747_963
    assert alerts == 348_460
    assert first == datetime(2005, 6, 3, 15, 42, 50, 363779)
    assert last == datetime(2006, 1, 4, 8, 0, 5, 233639)


def test_bgl_columns_are_parsed_on_every_line(database_url: str) -> None:
    counts = one(
        database_url,
        "SELECT count(ts), count(node), count(component), count(level), count(message), "
        f"count(*) FILTER (WHERE level NOT IN {BGL_LEVELS}) FROM {{}}",
        "bgl",
    )

    # 34,470 lines end at the severity, so their message is NULL; no level is ever a message word.
    assert counts == (4_747_963, 4_747_963, 4_747_963, 4_747_963, 4_713_493, 0)


def test_openstack_rows_and_time_range(database_url: str) -> None:
    rows, first, last = one(database_url, "SELECT count(*), min(ts), max(ts) FROM {}", "openstack")

    assert rows == 207_820
    assert first == datetime(2017, 5, 14, 19, 39, 1, 445000)
    assert last == datetime(2017, 5, 17, 12, 2, 35, 320000)


def test_openstack_columns_are_parsed_on_every_line(database_url: str) -> None:
    counts = one(
        database_url,
        "SELECT count(ts), count(pid), count(level), count(component), count(message) FROM {}",
        "openstack",
    )

    # 8 ERROR oslo_service.periodic_task lines have no message text.
    assert counts == (207_820, 207_820, 207_820, 207_820, 207_812)
