"""Fixtures for the corpus tests that read the canonical tables built by `make derive`."""

from __future__ import annotations

import os

import psycopg
import pytest

from serenedb_log_mcp.config import load_settings


@pytest.fixture(scope="session")
def slm_schema(database_url: str) -> str:
    """SLM_SCHEMA, once `make derive` has built the canonical tables there; skips otherwise."""
    schema = load_settings().slm_schema
    with psycopg.connect(database_url, autocommit=True) as conn:
        row = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s "
            "AND table_name IN ('bgl_entry', 'os_entry', 'ownership')",
            [schema],
        ).fetchone()
    if not row or row[0] < 3:
        problem = f"the canonical tables are missing in schema {schema!r}; run `make derive`"
        if os.environ.get("SLM_REQUIRE_DB", "").strip().lower() in {"1", "true", "yes", "on"}:
            pytest.fail(f"SLM_REQUIRE_DB is set, so this does not skip: {problem}", pytrace=False)
        pytest.skip(problem)
    return schema
