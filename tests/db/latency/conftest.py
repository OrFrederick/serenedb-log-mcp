"""Fixtures for the latency_stats db tests: the synthetic OpenStack corpus in a throwaway schema.

`latency_schema` builds tests/support/latency_corpus.py into `scratch_t11_<hex>` once per session
and drops it afterwards. Every test then runs with SLM_SCHEMA pointing at it, an empty in-memory
Evidence Ledger and the identity Role hook, so no call can touch the shared Base Corpus.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import latency_corpus
import pytest

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings


@pytest.fixture(scope="session")
def latency_schema(database_url: str) -> Iterator[str]:
    schema = f"scratch_t11_{uuid.uuid4().hex[:8]}"
    try:
        latency_corpus.build(database_url, schema)
        yield schema
    finally:
        latency_corpus.drop(database_url, schema)


@pytest.fixture(autouse=True)
def synthetic(latency_schema: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The synthetic corpus as this test's SLM_SCHEMA."""
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=latency_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    return latency_schema
