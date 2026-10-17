"""Fixtures for the retrieval tools' db tests: the synthetic corpus in a throwaway schema.

`corpus` builds tests/support/retrieval_corpus.py into `scratch_t5_<hex>` once per session and drops
it afterwards. Every test then runs with SLM_SCHEMA pointing at it, an empty in-memory Evidence
Ledger, the identity Role hook and empty fact caches, so the calls cannot touch the shared Base
Corpus.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
import retrieval_corpus

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts


@pytest.fixture(scope="session")
def corpus_schema(database_url: str) -> Iterator[str]:
    schema = f"scratch_t5_{uuid.uuid4().hex[:8]}"
    try:
        retrieval_corpus.build(database_url, schema)
        yield schema
    finally:
        retrieval_corpus.drop(database_url, schema)


@pytest.fixture
def corpus(corpus_schema: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The synthetic corpus as this test's SLM_SCHEMA."""
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=corpus_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    return corpus_schema
