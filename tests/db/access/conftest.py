"""Fixtures for the access-control db tests (T22): the Loghub 2k samples, derived into a throwaway
schema (`derived_sample`), with sql/access/roles.sql applied for LOGIN roles of unique names
(tests/support/access_setup.py). Both are dropped at the end of the session, and the teardown checks
that nothing is left on the shared server.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import access_setup
import pytest
from access_setup import Access


@pytest.fixture(scope="session")
def granted(derived_sample: Any, database_url: str) -> Iterator[Access]:
    """The derived samples with per-Role views and three fresh LOGIN roles."""
    found: Access | None = None
    try:
        found = access_setup.create(database_url, derived_sample.slm, derived_sample.raw)
        yield found
    finally:
        if found is not None:
            access_setup.drop(found)
