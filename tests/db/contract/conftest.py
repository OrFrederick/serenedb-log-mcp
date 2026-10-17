"""Fixtures for the db contract tests (T15): the Loghub 2k samples, derived, as SLM_SCHEMA.

`sample` points SLM_SCHEMA at `derived_sample` (tests/db/conftest.py: the samples plus a few
synthetic rows, in a throwaway schema that is dropped afterwards), with an empty in-memory Evidence
Ledger, the product's Role shaping (what a client really receives) and empty fact caches. The calls
never touch the shared Base Corpus.
"""

from __future__ import annotations

from typing import Any

import pytest

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts


@pytest.fixture
def sample(derived_sample: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    """The derived samples as this test's SLM_SCHEMA."""
    schema: str = derived_sample.slm
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.DEFAULT_ROLE_HOOK)
    facts.clear_cache()
    return schema
