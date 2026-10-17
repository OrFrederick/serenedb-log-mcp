"""Command line entry point: `serenedb-log-mcp [--transport stdio|http] [--port N] [--role ROLE]`.

Startup logging goes to stderr only. Under `--transport stdio` stdout carries the JSON-RPC stream,
so anything printed there would corrupt the protocol.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from serenedb_log_mcp import __version__
from serenedb_log_mcp.config import ROLES, ConfigError, Settings, load_settings, safe_dsn, set_settings
from serenedb_log_mcp.ledger import LedgerError, get_ledger
from serenedb_log_mcp.server import create_server

PACKAGE_LOGGER = "serenedb_log_mcp"
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="serenedb-log-mcp",
        description=(
            "Read-only MCP server for Role-specific log search and aggregation over SereneDB. "
            "Configuration comes from the environment: DATABASE_URL, DATABASE_URL_<ROLE>, "
            "SLM_SCHEMA, SLM_RAW_SCHEMA, SLM_DATA_DIR, SLM_QUERY_TIMEOUT_S, SLM_CLIENT_BINDING, "
            "LEDGER_PATH, SLM_EXPORT_DIR and SLM_PATTERN_TIER1."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio for Claude Code and Claude Desktop (default); http serves streamable HTTP at /mcp",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address for --transport http")
    parser.add_argument("--port", type=int, default=8000, help="port for --transport http (default 8000)")
    parser.add_argument(
        "--role",
        choices=ROLES,
        default=None,
        help="pin every tool call to one Role (default: the caller passes the Role per call)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _log_resolved_configuration(settings: Settings) -> None:
    """One line saying which instance and which schemas this process will use, without a password.

    An answer is only worth as much as the instance it came from, so the log has to say which one
    answered. `safe_dsn` keeps every secret out of the line.
    """
    dsn = settings.database_url if settings.role is None else settings.database_url_for(settings.role)
    logger.info(
        "resolved configuration: dsn=%s slm_schema=%s raw_schema=%s role=%s",
        safe_dsn(dsn),
        settings.slm_schema,
        settings.raw_schema,
        settings.role or "per call",
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    # stderr, so the stdio transport's JSON-RPC stream on stdout stays clean. basicConfig does
    # nothing when the embedding application has already configured logging. INFO is for this
    # package only: the root stays at WARNING, so the MCP and HTTP libraries do not narrate.
    logging.basicConfig(
        stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger(PACKAGE_LOGGER).setLevel(logging.INFO)
    try:
        settings = load_settings(role=args.role)
    except (ConfigError, ValidationError) as exc:
        parser.error(str(exc))
    set_settings(settings)
    _log_resolved_configuration(settings)
    # The Evidence Ledger is loaded here, so a broken LEDGER_PATH is a usage error now instead of a
    # ToolError in the middle of an investigation. A path that is a directory or that this process
    # may not read raises OSError rather than LedgerError, and is just as much a usage error.
    try:
        get_ledger()
    except (LedgerError, OSError) as exc:
        parser.error(f"LEDGER_PATH: {exc}")
    server = create_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)
