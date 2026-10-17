"""The resolver against the derived sample: every name it returns exists and has the listed columns."""

from __future__ import annotations

from typing import Protocol

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
