#!/usr/bin/env python3
"""Build the canonical tables in SLM_SCHEMA from the loader's raw tables (plan sections 2.1, 2.2).

Usage (see `make help`):
    uv run python scripts/derive.py [--adapter dev|official] [--schema S] [--raw-schema R]

Runs, in this order and inside SLM_SCHEMA:
    1. sql/adapters/<adapter>.sql   the only loader-specific SQL: raw tables -> adapter views
    2. sql/seed/*.sql               in name order: the stub Ownership Table (TODO(organiser))
    3. sql/derive/*.sql             in name order: canonical tables, ..., inverted indexes last

Every step is re-runnable: tables are rebuilt with CREATE OR REPLACE, indexes are dropped and
built again, so a second run gives the same tables. Afterwards it prints a report: row counts,
the BGL node names without a node card, the OpenStack derived-column coverage and any Component
without a Team.

SQL files use two kinds of placeholders, replaced before execution:
    ${slm}, ${raw}                   the quoted canonical and raw schema names
    ${create_log_text_dictionary}    the dictionary DDL for the server version: the
                                     `AS split_text(...)` form on SereneDB 26.10.1 or later, the
                                     legacy `(TEMPLATE = 'text', ...)` form before that

Environment: DATABASE_URL, SLM_SCHEMA (default slm), SLM_RAW_SCHEMA (default public).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from serenedb_log_mcp.config import SCHEMA_RE, ConfigError, load_settings

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
SPLIT_TEXT_SINCE = (26, 10, 1)
PLACEHOLDER_RE = re.compile(r"\$\{([a-z_]+)\}")
VERSION_RE = re.compile(r"SereneDB v?(\d+(?:\.\d+)*)")


# The contract between sql/adapters/<adapter>.sql and the derive steps: the views every adapter
# creates in SLM_SCHEMA and their exact columns (documented in sql/adapters/official.sql).
ADAPTER_VIEWS: dict[str, tuple[str, ...]] = {
    "bgl_adapted": ("entry_id", "ts", "epoch_utc", "node", "component", "level", "alert_tag", "message"),
    "os_adapted": (
        "entry_id",
        "ts",
        "epoch_utc",
        "source_id",
        "log_file",
        "pid",
        "component",
        "level",
        "request_id",
        "instance_id",
        "message",
    ),
}


class DeriveError(Exception):
    """A problem the user can fix; printed without a traceback."""


# ----------------------------------------------------------------- SQL files


def split_statements(text: str) -> list[str]:
    """Split SQL text on `;` outside quotes and comments; drop comments and empty statements."""
    statements: list[str] = []
    current: list[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char in "'\"":
            end = i + 1
            while True:
                end = text.find(char, end)
                if end < 0:
                    raise DeriveError(f"unterminated {char} quote in: {text[i : i + 60]!r}")
                if end + 1 < n and text[end + 1] == char:
                    end += 2
                    continue
                break
            current.append(text[i : end + 1])
            i = end + 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise DeriveError("unterminated /* comment")
            i = end + 2
        elif char == ";":
            statements.append("".join(current))
            current = []
            i += 1
        else:
            current.append(char)
            i += 1
    statements.append("".join(current))
    return [s.strip() for s in statements if s.strip()]


def render(text: str, values: Mapping[str, str]) -> str:
    """Replace every `${name}` placeholder; an unknown name is an error."""

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            known = ", ".join(f"${{{k}}}" for k in sorted(values))
            raise DeriveError(f"unknown placeholder ${{{name}}}; known: {known}")
        return values[name]

    return PLACEHOLDER_RE.sub(substitute, text)


def serenedb_version(version: str) -> tuple[int, ...]:
    """The SereneDB version from `SELECT version()`, for example (26, 10, 1)."""
    match = VERSION_RE.search(version)
    if match is None:
        raise DeriveError(f"version() does not name a SereneDB release: {version!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def dictionary_ddl(name: str, version: tuple[int, ...]) -> str:
    """The DDL for the log text dictionary: lower-case words, no stemming, with the feature flags
    phrase search (position), highlighting (offset) and BM25 (frequency, norm) need."""
    if version >= SPLIT_TEXT_SINCE:
        return (
            f"CREATE TEXT SEARCH DICTIONARY {name} AS split_text(case := 'lower') "
            "WITH (frequency, position, offset, norm)"
        )
    # Before 26.10.1 the AS form is a syntax error. OrFrederick's Claude verified this form with
    # these flags on 26.09.1.3 (#2); it cannot be tested on the 26.10.1 dev instance.
    return (
        f"CREATE TEXT SEARCH DICTIONARY {name} (TEMPLATE = 'text', locale = 'en_US.UTF-8', "
        "\"case\" = 'lower', stemming = false, accent = false, frequency = true, position = true, "
        '"offset" = true, norm = true)'
    )


def _quoted(schema: str) -> str:
    if not SCHEMA_RE.fullmatch(schema):
        raise DeriveError(f"schema {schema!r} is not a plain lowercase identifier ({SCHEMA_RE.pattern})")
    return f'"{schema}"'


def variables(slm: str, raw: str, version: tuple[int, ...]) -> dict[str, str]:
    """The placeholder values for one run."""
    slm_q, raw_q = _quoted(slm), _quoted(raw)
    return {
        "slm": slm_q,
        "raw": raw_q,
        "create_log_text_dictionary": dictionary_ddl(f"{slm_q}.log_text", version),
    }


def steps(adapter: str, sql_dir: Path = SQL_DIR) -> list[Path]:
    """The SQL files of one run, in execution order."""
    adapter_file = sql_dir / "adapters" / f"{adapter}.sql"
    if not adapter_file.is_file():
        available = ", ".join(sorted(p.stem for p in (sql_dir / "adapters").glob("*.sql")))
        raise DeriveError(f"unknown adapter {adapter!r}; available: {available}")
    return [
        adapter_file,
        *sorted((sql_dir / "seed").glob("*.sql")),
        *sorted((sql_dir / "derive").glob("*.sql")),
    ]


def adapter_problems(found: Mapping[str, Sequence[str]]) -> list[str]:
    """How the adapter views in `found` (view -> columns) differ from ADAPTER_VIEWS."""
    problems = []
    for view, expected in ADAPTER_VIEWS.items():
        if view not in found:
            problems.append(f"{view} is missing")
            continue
        columns = set(found[view])
        problems += [f"{view} lacks {c}" for c in expected if c not in columns]
        problems += [f"{view} has extra column {c}" for c in sorted(columns - set(expected))]
    return problems


# ----------------------------------------------------------------- running


@dataclass(frozen=True)
class Timing:
    step: str
    statement: str
    seconds: float


def _summary(statement: str) -> str:
    first = " ".join(statement.split())
    return first if len(first) <= 90 else first[:87] + "..."


def run(conn: psycopg.Connection[Any], files: Sequence[Path], values: Mapping[str, str]) -> list[Timing]:
    """Execute every statement of `files` in order; print and return the timings."""
    timings: list[Timing] = []
    for path in files:
        step = f"{path.parent.name}/{path.name}"
        for statement in split_statements(render(path.read_text(), values)):
            started = time.perf_counter()
            conn.execute(statement)
            timing = Timing(step, _summary(statement), time.perf_counter() - started)
            timings.append(timing)
            print(f"{timing.seconds:8.2f}s  {step:28s} {timing.statement}", flush=True)
    return timings


def _rows(conn: psycopg.Connection[Any], query: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in conn.execute(query, params or None).fetchall()]


def relation_columns(conn: psycopg.Connection[Any], schema: str) -> dict[str, list[tuple[str, str]]]:
    """Every table and view of `schema` with its (column, type) list, in column order.

    information_schema.columns lists no view columns on SereneDB 26.10.1, so this uses DESCRIBE.
    """
    names = _rows(
        conn, "SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY 1", [schema]
    )
    return {
        name: [(str(r[0]), str(r[1])) for r in _rows(conn, f"DESCRIBE {_quoted(schema)}.{_quoted(name)}")]
        for (name,) in names
    }


def check_adapter(conn: psycopg.Connection[Any], slm: str, adapter: str) -> None:
    """Stop with a clear message when the adapter's views break the contract in ADAPTER_VIEWS."""
    relations = relation_columns(conn, slm)
    found = {view: [name for name, _ in relations[view]] for view in ADAPTER_VIEWS if view in relations}
    if problems := adapter_problems(found):
        raise DeriveError(f"sql/adapters/{adapter}.sql breaks the adapter contract: " + "; ".join(problems))


def report(conn: psycopg.Connection[Any], slm: str) -> list[str]:
    """Row counts and coverage of the derived columns, one line each."""
    s = _quoted(slm)
    lines = []
    for table in ("bgl_entry", "os_entry", "ownership"):
        (count,) = _rows(conn, f"SELECT count(*) FROM {s}.{table}")[0]
        lines.append(f"{slm}.{table}: {count:,} rows")
    no_card, no_mid, no_rack = _rows(
        conn,
        f"SELECT count(*) FILTER (WHERE nodecard IS NULL), count(*) FILTER (WHERE midplane IS NULL), "
        f"count(*) FILTER (WHERE rack IS NULL) FROM {s}.bgl_entry",
    )[0]
    lines.append(
        f"bgl_entry: {no_card:,} Log Entries have a node name outside Rxx-My-Nz-... (nodecard NULL); "
        f"{no_mid:,} have no midplane and {no_rack:,} no rack"
    )
    for node, count in _rows(
        conn,
        f"SELECT node, count(*) AS n FROM {s}.bgl_entry WHERE nodecard IS NULL "
        "GROUP BY node ORDER BY n DESC, node LIMIT 8",
    ):
        lines.append(f"  {count:>10,}  {node}")
    rows, endpoint, status, latency, instance = _rows(
        conn,
        f"SELECT count(*), count(endpoint), count(http_status), count(latency_s), count(instance_ref) "
        f"FROM {s}.os_entry",
    )[0]
    lines.append(
        f"os_entry: {endpoint:,} with endpoint, {status:,} with http_status, {latency:,} with latency_s, "
        f"{instance:,} with instance_ref, of {rows:,}"
    )
    sources = _rows(conn, f"SELECT source_id, count(*) FROM {s}.os_entry GROUP BY 1 ORDER BY 1")
    lines.append("os_entry sources: " + ", ".join(f"{sid} {count:,}" for sid, count in sources))
    unmapped = _rows(
        conn,
        f"SELECT e.dataset, e.component FROM ("
        f"SELECT DISTINCT 'bgl' AS dataset, component FROM {s}.bgl_entry UNION "
        f"SELECT DISTINCT 'openstack', component FROM {s}.os_entry) e "
        f"LEFT JOIN {s}.ownership o ON o.dataset = e.dataset AND o.component = e.component "
        "WHERE e.component IS NOT NULL AND o.team IS NULL ORDER BY 1, 2",
    )
    lines.append(
        "Components without a Team: " + (", ".join(f"{d}/{c}" for d, c in unmapped) if unmapped else "none")
    )
    return lines


# ----------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="derive.py",
        description="Build the canonical tables, the stub Ownership Table and the inverted indexes.",
    )
    parser.add_argument("--adapter", default="dev", help="sql/adapters/<adapter>.sql (default: dev)")
    parser.add_argument("--schema", help="target schema (default: SLM_SCHEMA, else slm)")
    parser.add_argument("--raw-schema", help="schema of the loader's raw tables (default: SLM_RAW_SCHEMA)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        slm = args.schema or settings.slm_schema
        raw = args.raw_schema or settings.raw_schema
        slm_q, _ = _quoted(slm), _quoted(raw)
        if slm == raw:
            raise DeriveError(
                f"SLM_SCHEMA and SLM_RAW_SCHEMA are both {slm!r}; derive into its own schema (default slm)"
            )
        files = steps(args.adapter)
        if not split_statements(files[0].read_text()):
            raise DeriveError(
                f"{files[0].relative_to(SQL_DIR.parent)} has no statements yet: TODO(organiser), "
                "it is filled in when the organisers' loader arrives (T25)"
            )
        started = time.perf_counter()
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            row = conn.execute("SELECT version()").fetchone()
            version = serenedb_version(str(row[0]) if row else "")
            print(f"SereneDB {'.'.join(map(str, version))}: deriving {raw} -> {slm} ({args.adapter})")
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {slm_q}")
            values = variables(slm, raw, version)
            run(conn, files[:1], values)
            check_adapter(conn, slm, args.adapter)
            run(conn, files[1:], values)
            print(f"derive finished in {time.perf_counter() - started:.1f}s")
            for line in report(conn, slm):
                print(line)
    except (DeriveError, ConfigError) as exc:
        print(f"derive: {exc}", file=sys.stderr)
        return 2
    except psycopg.Error as exc:
        # Every step is re-runnable, so after a lost connection (the server restarts after a
        # crash) running it again is safe.
        print(f"derive: {type(exc).__name__}: {exc}; once the database is up, run it again", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
