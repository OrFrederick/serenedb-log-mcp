"""Fixtures for the Pattern catalogue tools' db tests (T14).

They run on `derived_sample` (tests/db/conftest.py): the Loghub 2k samples derived by the real
derive step into a throwaway schema, so the catalogue table and its search index are the ones
`make derive` builds. Every test runs with SLM_SCHEMA pointing at it, an empty in-memory Evidence
Ledger, the identity Role hook and empty fact caches, so no call can touch the shared Base Corpus.
"""

from __future__ import annotations

from typing import Protocol

import pytest

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts


class Schemas(Protocol):
    raw: str
    slm: str


@pytest.fixture
def sample(derived_sample: Schemas, monkeypatch: pytest.MonkeyPatch) -> str:
    """The derived sample as this test's SLM_SCHEMA."""
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=derived_sample.slm))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    return derived_sample.slm
