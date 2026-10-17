"""Fixtures for the golden tests: the full Base Corpus in SLM_SCHEMA (default slm).

Golden tests are marked `corpus`, so `make test-corpus` runs them and they skip with the reason
(fail with SLM_REQUIRE_DB=1) when the Base Corpus is missing. Each test runs with SLM_SCHEMA set,
an empty in-memory Evidence Ledger, the identity Role hook and empty fact caches.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts


@pytest.fixture(scope="session")
def golden_schema(database_url: str) -> str:
    """SLM_SCHEMA, once `make derive` has built the canonical tables and indexes there."""
    schema = load_settings().slm_schema
    with psycopg.connect(database_url, autocommit=True) as conn:
        tables = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s "
            "AND table_name IN ('bgl_entry', 'os_entry', 'ownership')",
            [schema],
        ).fetchone()
        indexes = conn.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname = %s "
            "AND indexname IN ('bgl_entry_idx', 'os_entry_idx')",
            [schema],
        ).fetchone()
    if not tables or tables[0] < 3 or not indexes or indexes[0] < 2:
        problem = f"the canonical tables or indexes are missing in schema {schema!r}; run `make derive`"
        if os.environ.get("SLM_REQUIRE_DB", "").strip().lower() in {"1", "true", "yes", "on"}:
            pytest.fail(f"SLM_REQUIRE_DB is set, so this does not skip: {problem}", pytrace=False)
        pytest.skip(problem)
    return schema


@pytest.fixture(autouse=True)
def golden_settings(golden_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=golden_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
