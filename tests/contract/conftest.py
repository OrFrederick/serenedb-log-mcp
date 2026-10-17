"""Isolation for the contract tests: default settings and an empty in-process Evidence Ledger.

These tests speak MCP, in process or over stdio, so they must not pick up a developer's own
`LEDGER_PATH`; the two-process test sets its own in the child processes' environment.
"""

import pytest

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings


@pytest.fixture(autouse=True)
def pure_process_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_active", load_settings({}))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
