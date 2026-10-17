"""The Loghub 2k samples for the db tests, with the one rule about when a missing sample may skip.

A test may only skip when the network is at fault. Permission denied, a read-only filesystem and a
full disk are environment faults: they would turn a whole db module into a silent pass, so they are
left to fail. `SLM_REQUIRE_DB` (CI, PR gates) turns even the network skip into a failure.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.error import URLError

import pytest

import dev_loader
from serenedb_log_mcp.config import load_settings

# URLError covers a refused connection, DNS failure and a connect timeout; a read that times out
# mid-download raises TimeoutError instead. Both are subclasses of OSError, which is why catching
# OSError here would also swallow EACCES, EROFS and ENOSPC.
NETWORK_ERRORS = (URLError, TimeoutError)


def samples_or_skip() -> list[Path]:
    """The local paths of the Loghub 2k samples, downloading them on first use.

    Skips the test when the download fails for a network reason, unless SLM_REQUIRE_DB is set.
    Every other error is raised: a checksum mismatch and an unwritable directory are real failures.
    """
    try:
        return dev_loader.ensure_samples(load_settings().data_dir / "sample")
    except NETWORK_ERRORS as exc:
        problem = f"cannot download the Loghub 2k samples: {exc}"
        if os.environ.get("SLM_REQUIRE_DB", "").strip().lower() in {"1", "true", "yes", "on"}:
            pytest.fail(f"SLM_REQUIRE_DB is set, so this does not skip: {problem}", pytrace=False)
        pytest.skip(problem)
