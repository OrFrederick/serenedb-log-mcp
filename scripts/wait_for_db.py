#!/usr/bin/env python3
"""Wait until SereneDB answers `SELECT 1`, or fail after a timeout (used by `make db-up`).

    uv run python scripts/wait_for_db.py [--dsn DSN] [--timeout 90] [--interval 1]

Exit 0 when the server answers, 1 when it does not within the timeout, 2 on bad arguments.
The DSN defaults to DATABASE_URL. Messages never print the password.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Sequence

import psycopg
from psycopg.conninfo import conninfo_to_dict

from serenedb_log_mcp.config import load_settings


def _safe(dsn: str) -> str:
    try:
        parts = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        return "<unparseable DSN>"
    return " ".join(f"{key}={value}" for key, value in parts.items() if "password" not in key)


def _positive_seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"{text!r} must be a finite number of seconds greater than 0")
    return value


def wait(dsn: str, timeout_s: float, interval_s: float) -> tuple[str | None, str]:
    """Poll until the server answers. Return (version, "") on success, (None, last error) on timeout."""
    deadline = time.monotonic() + timeout_s
    last_error = "no attempt made"
    while True:
        remaining = deadline - time.monotonic()
        try:
            connect_timeout = max(1, math.ceil(min(remaining, 5)))
            with psycopg.connect(dsn, connect_timeout=connect_timeout, autocommit=True) as conn:
                conn.execute("SELECT 1")
                row = conn.execute("SELECT version()").fetchone()
            return (str(row[0]) if row else "unknown version"), ""
        except psycopg.Error as exc:
            last_error = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, last_error
        time.sleep(min(interval_s, remaining))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wait_for_db.py", description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", help="connection string (default: DATABASE_URL)")
    parser.add_argument(
        "--timeout", type=_positive_seconds, default=90.0, help="seconds to wait (default 90)"
    )
    parser.add_argument("--interval", type=_positive_seconds, default=1.0, help="seconds between tries")
    args = parser.parse_args(argv)
    dsn = args.dsn or load_settings().database_url
    version, error = wait(dsn, args.timeout, args.interval)
    if version is None:
        print(
            f"wait_for_db: SereneDB at {_safe(dsn)} did not answer within {args.timeout:g} s. "
            f"Last error: {error}",
            file=sys.stderr,
        )
        return 1
    print(f"SereneDB is ready at {_safe(dsn)}: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
