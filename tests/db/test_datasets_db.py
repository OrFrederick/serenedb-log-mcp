"""The resolver against the derived sample: every name it returns exists and has the listed columns."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg import sql

from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus.datasets import DATASET_NAMES, relation


class Schemas(Protocol):
    raw: str
    slm: str


@pytest.mark.parametrize("name", DATASET_NAMES)
def test_the_resolved_table_has_exactly_the_registered_columns(
    database_url: str, derived_sample: Schemas, name: str
) -> None:
    rel = relation(name, "engineer", None, settings=load_settings(slm_schema=derived_sample.slm))

    with psycopg.connect(database_url, autocommit=True) as conn:
        described = conn.execute(sql.SQL("DESCRIBE {}").format(rel.table)).fetchall()

    assert tuple(row[0] for row in described) == rel.dataset.table_columns


@pytest.mark.parametrize(("name", "term"), [("bgl", "parity error"), ("openstack", "instance")])
def test_the_resolved_index_answers_full_text_and_filters(
    database_url: str, derived_sample: Schemas, name: str, term: str
) -> None:
    rel = relation(name, "team_lead", None, settings=load_settings(slm_schema=derived_sample.slm))
    query = sql.SQL(
        "SELECT count(*), count({source}) FROM {index} WHERE {message} @@ websearch_to_tsquery(%s)"
    )

    with psycopg.connect(database_url, autocommit=True) as conn:
        indexed = conn.execute(
            query.format(index=rel.index, source=rel.column("source"), message=rel.column("message")),
            [term],
        ).fetchone()
        joined = conn.execute(
            sql.SQL(
                "SELECT count(*) FROM {table} e JOIN {owners} o ON o.component = e.{component} "
                "AND o.dataset = %s"
            ).format(table=rel.table, owners=rel.ownership_table, component=rel.column("component")),
            [rel.dataset.name],
        ).fetchone()

    assert indexed is not None
    assert indexed[0] > 0
    assert joined is not None
    assert joined[0] > 0


def _utc_seconds(ts: datetime, zone: ZoneInfo) -> set[float]:
    """Every UTC instant a local `ts` can be in `zone`: two in the repeated fall-back hour."""
    return {ts.replace(tzinfo=zone, fold=fold).timestamp() for fold in (0, 1)}


@pytest.mark.parametrize("name", DATASET_NAMES)
def test_ts_read_in_the_registered_zone_gives_epoch_utc(
    database_url: str, derived_sample: Schemas, name: str
) -> None:
    # Pins Dataset.ts_zone to the data: the derive step computes epoch_utc without it (#48).
    rel = relation(name, settings=load_settings(slm_schema=derived_sample.slm))
    zone, utc = ZoneInfo(rel.dataset.ts_zone), ZoneInfo("UTC")

    with psycopg.connect(database_url, autocommit=True) as conn:
        entries = conn.execute(
            sql.SQL("SELECT entry_id, {ts}, {epoch} FROM {table}").format(
                ts=rel.column("time"), epoch=rel.column("epoch"), table=rel.table
            )
        ).fetchall()

    def disagrees(ts: datetime, epoch: float, in_zone: ZoneInfo) -> bool:
        return all(abs(seconds - epoch) > 1e-5 for seconds in _utc_seconds(ts, in_zone))

    assert len(entries) >= 2000
    assert [entry_id for entry_id, ts, epoch in entries if disagrees(ts, epoch, zone)] == []
    if rel.dataset.ts_is_local:
        # The check tells the zones apart: read as UTC, no BGL ts gives its epoch_utc.
        assert all(disagrees(ts, epoch, utc) for _, ts, epoch in entries)
