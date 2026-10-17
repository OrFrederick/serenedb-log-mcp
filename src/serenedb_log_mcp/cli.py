"""Command line entry point: `serenedb-log-mcp [--transport stdio|http] [--port N] [--role ROLE]`."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from pydantic import ValidationError

from serenedb_log_mcp import __version__
from serenedb_log_mcp.config import ROLES, ConfigError, load_settings, set_settings
from serenedb_log_mcp.ledger import LedgerError, get_ledger
from serenedb_log_mcp.server import create_server


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


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(role=args.role)
    except (ConfigError, ValidationError) as exc:
        parser.error(str(exc))
    set_settings(settings)
    # The Evidence Ledger is loaded here, so a broken LEDGER_PATH is a usage error now instead of a
    # ToolError in the middle of an investigation.
    try:
        get_ledger()
    except LedgerError as exc:
        parser.error(f"LEDGER_PATH: {exc}")
    server = create_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)
