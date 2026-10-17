"""Fixtures for the top_patterns db tests: the synthetic corpus in a throwaway schema.

`top_schema` builds tests/support/top_patterns_corpus.py into `scratch_t28_<hex>` once per session
and drops it afterwards. Every test then runs with SLM_SCHEMA pointing at it, an empty in-memory
Evidence Ledger and the identity Role hook, so no call can touch the shared Base Corpus.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
import top_patterns_corpus

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts


@pytest.fixture(scope="session")
def top_schema(database_url: str) -> Iterator[str]:
    schema = f"scratch_t28_{uuid.uuid4().hex[:8]}"
    try:
        top_patterns_corpus.build(database_url, schema)
        yield schema
    finally:
        top_patterns_corpus.drop(database_url, schema)


@pytest.fixture(autouse=True)
def synthetic(top_schema: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The synthetic corpus as this test's SLM_SCHEMA."""
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=top_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    return top_schema
