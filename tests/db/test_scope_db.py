"""`scope.py` against the raw BGL table in SLM_RAW_SCHEMA (read-only; the 2k sample is enough)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest

from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.db import Database, QueryFailed
from serenedb_log_mcp.scope import ScopeError, build_scope, time_bounds, validate_choice

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def raw_bgl() -> Iterator[tuple[Database, str]]:
    settings = load_settings()
    layer = Database(settings.database_url)
    table = f"{settings.raw_schema}.bgl"  # raw_schema is validated as a plain identifier
    try:
        layer.fetch_all(f"SELECT 1 FROM {table} LIMIT 1")
    except QueryFailed as exc:
        layer.close()
        pytest.skip(f"{table} is not loaded ({exc}); load it with `make load-sample`")
    yield layer, table
    layer.close()


def test_kernal_suggests_kernel_from_the_real_components(raw_bgl: tuple[Database, str]) -> None:
    layer, table = raw_bgl
    components = [str(row[0]) for row in layer.fetch_all(f"SELECT DISTINCT component FROM {table}").rows]

    with pytest.raises(ScopeError, match="Did you mean 'KERNEL'"):
        validate_choice("component", "KERNAL", components)


def test_last_7d_covers_the_last_week_of_the_dataset(raw_bgl: tuple[Database, str]) -> None:
    layer, table = raw_bgl
    last = layer.fetch_all(f"SELECT max(ts) FROM {table}").rows[0][0]
    assert isinstance(last, datetime)

    scope = build_scope("bgl", time_from="last 7d", corpus_end={"bgl": last})
    start, end = time_bounds(scope)
    inside = layer.fetch_all(
        f"SELECT count(*), max(ts) FROM {table} WHERE ts >= %s AND ts < %s", [start, end]
    )

    count, latest = inside.rows[0]
    assert count > 0
    assert latest == last
    assert scope.time_input == "last 7d"
    assert scope.resolved_against == f"bgl corpus end {last.isoformat()}"
