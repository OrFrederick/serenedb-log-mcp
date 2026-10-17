"""Fixtures for the aggregation tools' db tests: the synthetic corpus in a throwaway schema.

`corpus` builds tests/support/aggregation_corpus.py into `scratch_t6_<hex>` once per session and
drops it afterwards. Every test then runs with SLM_SCHEMA pointing at it, an empty in-memory
Evidence Ledger and the identity Role hook, so the calls cannot touch the shared Base Corpus.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import aggregation_corpus
import pytest

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts


@pytest.fixture(scope="session")
def corpus_schema(database_url: str) -> Iterator[str]:
    schema = f"scratch_t6_{uuid.uuid4().hex[:8]}"
    try:
        aggregation_corpus.build(database_url, schema)
        yield schema
    finally:
        aggregation_corpus.drop(database_url, schema)


@pytest.fixture
def corpus(corpus_schema: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The synthetic corpus as this test's SLM_SCHEMA."""
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=corpus_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    return corpus_schema
