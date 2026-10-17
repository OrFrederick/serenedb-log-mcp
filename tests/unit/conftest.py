"""Isolation for the pure unit tests.

Every test starts from the default settings, an empty process-wide Evidence Ledger and the
identity Role hook, so a developer's own `LEDGER_PATH` or `DATABASE_URL` cannot change what the
tests see, and a test that pins a Role, a ledger path or a Role hook cannot leak it into the next
one. `monkeypatch` restores the module globals afterwards, which is the isolation style the rest of
these tests already use.
"""

from pathlib import Path

import pytest

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings, set_settings


@pytest.fixture(autouse=True)
def pure_process_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_active", load_settings({}))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    """Configure `LEDGER_PATH` for this test and return it."""
    path = tmp_path / "ledger.jsonl"
    set_settings(load_settings({}, ledger_path=path))
    ledger.reset_ledger()
    return path
