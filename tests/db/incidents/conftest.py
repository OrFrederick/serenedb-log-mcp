"""Fixtures for the list_incidents db tests: the Incident lab corpus in a throwaway schema.

`incident_schema` builds tests/support/incident_corpus.py into `scratch_t27_<hex>` once per session
and drops it afterwards. Every test then runs with SLM_SCHEMA pointing at it, an empty in-memory
Evidence Ledger, the identity Role hook and empty fact caches, so no call touches the Base Corpus.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import incident_corpus
import pytest

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts


@pytest.fixture(scope="session")
def incident_schema(database_url: str) -> Iterator[str]:
    schema = f"scratch_t27_{uuid.uuid4().hex[:8]}"
    try:
        incident_corpus.build(database_url, schema)
        yield schema
    finally:
        incident_corpus.drop(database_url, schema)


@pytest.fixture(autouse=True)
def lab(incident_schema: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The lab corpus as this test's SLM_SCHEMA."""
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=incident_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    return incident_schema
