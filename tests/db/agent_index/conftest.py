"""Fixtures for the agent index db tests (T19): the synthetic corpus in a throwaway schema.

`index_schema` builds tests/support/aggregation_corpus.py (canonical tables, the `log_text`
dictionary and the full indexes) into `scratch_t19_<hex>` once per session and drops it afterwards,
so agent indexes are never built in the shared `slm` schema. `corpus` points SLM_SCHEMA at it for
one test, with an empty Evidence Ledger and agent index registry, and drops any agent index the
test left behind.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import aggregation_corpus
import psycopg
import pytest
from psycopg import sql

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import agent_indexes, facts


@pytest.fixture(scope="session")
def index_schema(database_url: str) -> Iterator[str]:
    schema = f"scratch_t19_{uuid.uuid4().hex[:8]}"
    try:
        aggregation_corpus.build(database_url, schema)
        yield schema
    finally:
        aggregation_corpus.drop(database_url, schema)


@pytest.fixture
def corpus(index_schema: str, database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The synthetic corpus as this test's SLM_SCHEMA, with no agent index before or after."""
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=index_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    agent_indexes.clear()
    yield index_schema
    agent_indexes.clear()
    with psycopg.connect(database_url, autocommit=True) as conn:
        for (name,) in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = %s AND indexname LIKE 'agent%%'",
            [index_schema],
        ).fetchall():
            conn.execute(sql.SQL("DROP INDEX IF EXISTS {}").format(sql.Identifier(index_schema, name)))
