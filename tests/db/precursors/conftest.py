"""Fixtures for the find_precursors and similar_to db tests: the precursor lab corpus in a throwaway schema.

`precursor_schema` builds tests/support/precursor_corpus.py into `scratch_t16_<hex>` once per session
and drops it afterwards. Every test then runs with SLM_SCHEMA pointing at it, an empty in-memory
Evidence Ledger, the identity Role hook and empty fact caches, so no call touches the Base Corpus.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import precursor_corpus
import pytest

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts


@pytest.fixture(scope="session")
def precursor_schema(database_url: str) -> Iterator[str]:
    schema = f"scratch_t16_{uuid.uuid4().hex[:8]}"
    try:
        precursor_corpus.build(database_url, schema)
        yield schema
    finally:
        precursor_corpus.drop(database_url, schema)


@pytest.fixture(autouse=True)
def lab(precursor_schema: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The lab corpus as this test's SLM_SCHEMA."""
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=precursor_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    return precursor_schema
