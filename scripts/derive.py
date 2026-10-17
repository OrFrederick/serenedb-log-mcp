#!/usr/bin/env python3
"""Build the canonical tables in SLM_SCHEMA from the loader's raw tables (plan sections 2.1, 2.2).

Usage (see `make help`):
    uv run python scripts/derive.py [--adapter dev|official] [--schema S] [--raw-schema R]
    uv run python scripts/derive.py --only 20_patterns [--only STEP ...]

Runs, in this order and inside SLM_SCHEMA:
    1. sql/adapters/<adapter>.sql   the only loader-specific SQL: raw tables -> two temporary
                                    adapter tables, checked against ADAPTER_TABLES
    2. sql/seed/*.sql               in name order: the stub Ownership Table (TODO(organiser))
    3. sql/derive/*.sql             in name order: canonical tables, ..., inverted indexes last

Every step is re-runnable: tables are rebuilt with CREATE OR REPLACE, indexes are dropped and
built again, so a second run gives the same tables. Afterwards it prints a report: row counts,
the BGL node names without a node card, the OpenStack derived-column coverage and any Component
without a Team. `--only` runs just the named sql/derive steps (file names without .sql), without
the adapter and the seeds, on canonical tables that already exist.

SQL files use two kinds of placeholders, replaced before execution:
    ${slm}, ${raw}                   the quoted canonical and raw schema names
    ${create_log_text_dictionary}    the dictionary DDL for the server version: the
                                     `AS split_text(...)` form on SereneDB 26.10.1 or later, the
                                     legacy `(TEMPLATE = 'text', ...)` form before that
    ${create_pattern_text_dictionary}
                                     the same choice for the Pattern catalogue's dictionary, which
                                     also stems English words (search_patterns, T14)
    ${pattern_tier1}                 the Tier 1 Pattern SQL that scripts/gen_pattern_sql.py
                                     generates from the Loghub-2.0 template files (T9)

Environment: DATABASE_URL, SLM_SCHEMA (default slm), SLM_RAW_SCHEMA (default public),
SLM_DATA_DIR and SLM_PATTERN_TIER1 (default 1; 0 derives Patterns without the Loghub-2.0 files).
Only a run that includes a step with ${pattern_tier1} reads the Loghub-2.0 files, so
`--only 90_indexes` works without them.
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

import gen_pattern_sql
from gen_pattern_sql import Template
from serenedb_log_mcp.config import ROLES, SCHEMA_RE, ConfigError, load_settings
from serenedb_log_mcp.corpus.datasets import OWNERSHIP_NOTE, OWNERSHIP_STUB_SOURCE, role_schema

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
SPLIT_TEXT_SINCE = (26, 10, 1)
CONNECT_TIMEOUT_S = 10
ENTRY_INDEXES = {"bgl_entry": "bgl_entry_idx", "os_entry": "os_entry_idx"}
# Every index a usable schema has: the entry indexes (90_indexes) and the Pattern catalogue's
# search index (20_patterns), which search_patterns reads.
REQUIRED_INDEXES = (*ENTRY_INDEXES.values(), "pattern_idx")
PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
VERSION_RE = re.compile(r"SereneDB v?(\d+(?:\.\d+)*)")


# The contract between sql/adapters/<adapter>.sql and the derive steps: the temporary tables every
# adapter creates (temp.main.<table>) with their exact columns and types, as DESCRIBE reports them
# (documented in sql/adapters/official.sql). Temporary tables die with the derive session and hold
# no dependency on the raw tables, so they never block a reload of them. Views would not do:
# a persistent view blocks DROP TABLE on the raw table (DependentObjectsStillExist), and a
# temporary view over a table cannot be read on SereneDB 26.09.1.3 (InsufficientPrivilege).
ADAPTER_TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "bgl_adapted": (
        ("entry_id", "BIGINT"),
        ("ts", "TIMESTAMP"),
        ("epoch_utc", "DOUBLE"),
        ("node", "VARCHAR"),
        ("component", "VARCHAR"),
        ("level", "VARCHAR"),
        ("alert_tag", "VARCHAR"),
        ("message", "VARCHAR"),
    ),
    "os_adapted": (
        ("entry_id", "BIGINT"),
        ("ts", "TIMESTAMP"),
        ("epoch_utc", "DOUBLE"),
        ("source_id", "VARCHAR"),
        ("log_file", "VARCHAR"),
        ("pid", "BIGINT"),
        ("component", "VARCHAR"),
        ("level", "VARCHAR"),
        ("request_id", "VARCHAR"),
        ("instance_id", "VARCHAR"),
        ("message", "VARCHAR"),
    ),
}


class DeriveError(Exception):
    """A problem the user can fix; printed without a traceback."""


class StepFailed(DeriveError):
    """A statement of a SQL step failed; carries the step, the statement and the database error."""

    def __init__(self, step: str, statement: str, cause: psycopg.Error) -> None:
        self.step = step
        self.statement = statement
        self.cause = cause
        super().__init__(f"{step} failed at `{statement}`: {type(cause).__name__}: {_first_line(cause)}")


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


def pattern_dictionary_ddl(name: str, version: tuple[int, ...]) -> str:
    """The DDL for the Pattern text dictionary: lower-case words with English (Snowball) stemming,
    so 'failing' and 'failed' meet at 'fail', with the flags BM25 needs (frequency, norm).

    search_patterns (T14) ranks the Pattern catalogue with it. The Log Entry text stays unstemmed
    (`dictionary_ddl`), because search_logs promises whole-word matches."""
    if version >= SPLIT_TEXT_SINCE:
        return (
            f"CREATE TEXT SEARCH DICTIONARY {name} AS split_text(case := 'lower') "
            "| stem_words('en_US.UTF-8') WITH (frequency, norm)"
        )
    # The legacy form of `dictionary_ddl` with stemming on, as the SereneDB cookbook describes it
    # ("stemming = true ... picked by locale"); like that form, untested on 26.09.1.3.
    return (
        f"CREATE TEXT SEARCH DICTIONARY {name} (TEMPLATE = 'text', locale = 'en_US.UTF-8', "
        "\"case\" = 'lower', stemming = true, accent = false, frequency = true, norm = true)"
    )


def _quoted(schema: str) -> str:
    if not SCHEMA_RE.fullmatch(schema):
        raise DeriveError(f"schema {schema!r} is not a plain lowercase identifier ({SCHEMA_RE.pattern})")
    return f'"{schema}"'


def _ident(name: str) -> str:
    """Any relation or column name, double-quoted for SQL."""
    return '"' + name.replace('"', '""') + '"'


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def variables(
    slm: str,
    raw: str,
    version: tuple[int, ...],
    tier1: Mapping[str, Sequence[Template]] | None = None,
) -> dict[str, str]:
    """The placeholder values for one run. `tier1` are the Loghub-2.0 templates per Dataset, or
    None when Pattern Tier 1 is off."""
    slm_q, raw_q = _quoted(slm), _quoted(raw)
    return {
        "slm": slm_q,
        "raw": raw_q,
        "create_log_text_dictionary": dictionary_ddl(f"{slm_q}.log_text", version),
        "create_pattern_text_dictionary": pattern_dictionary_ddl(f"{slm_q}.pattern_text", version),
        "pattern_tier1": gen_pattern_sql.tier1_sql(slm_q, tier1),
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


def select_steps(files: Sequence[Path], names: Sequence[str]) -> list[Path]:
    """The sql/derive steps of `files` named in `names` (file names without .sql), in run order."""
    derive_steps = {p.stem: p for p in files if p.parent.name == "derive"}
    for name in names:
        if name not in derive_steps:
            raise DeriveError(f"unknown derive step {name!r}; derive steps: {', '.join(derive_steps)}")
    return [p for p in files if p.parent.name == "derive" and p.stem in names]


def needs_tier1(files: Sequence[Path]) -> bool:
    """Whether any of `files` uses the ${pattern_tier1} placeholder, so the run needs the Loghub-2.0
    templates; `--only 90_indexes` does not."""
    return any("${pattern_tier1}" in path.read_text() for path in files)


def load_tier1(enabled: bool, data_dir: Path) -> dict[str, list[Template]] | None:
    """The Loghub-2.0 templates for Pattern Tier 1, or None when SLM_PATTERN_TIER1=0."""
    if not enabled:
        print("Pattern Tier 1 off (SLM_PATTERN_TIER1=0): every Pattern comes from the generic masks")
        return None
    try:
        templates = gen_pattern_sql.load_templates(data_dir)
    except (gen_pattern_sql.TemplatesMissingError, gen_pattern_sql.TemplateFileError) as exc:
        raise DeriveError(str(exc)) from None
    counts = ", ".join(f"{len(found)} for {dataset}" for dataset, found in templates.items())
    print(f"Pattern Tier 1 on: Loghub-2.0 templates, {counts}")
    return templates


def adapter_problems(
    found: Mapping[str, Sequence[tuple[str, str]]], reasons: Mapping[str, str] | None = None
) -> list[str]:
    """How the adapter tables in `found` (table -> (column, type) list) differ from ADAPTER_TABLES.

    `reasons` carries the database's error for a table that could not be described.
    """
    problems = []
    for table, expected in ADAPTER_TABLES.items():
        if table not in found:
            reason = (reasons or {}).get(table)
            problems.append(f"{table} is missing" + (f" ({reason})" if reason else ""))
            continue
        columns = dict(found[table])
        wanted = dict(expected)
        problems += [f"{table} lacks {c}" for c in wanted if c not in columns]
        problems += [f"{table} has extra column {c}" for c in sorted(set(columns) - set(wanted))]
        problems += [
            f"{table}.{c} is {columns[c]}, expected {kind}"
            for c, kind in expected
            if c in columns and columns[c] != kind
        ]
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
            try:
                conn.execute(statement)
            except psycopg.Error as exc:
                raise StepFailed(step, _summary(statement), exc) from exc
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
        name: [(str(r[0]), str(r[1])) for r in _rows(conn, f"DESCRIBE {_quoted(schema)}.{_ident(name)}")]
        for (name,) in names
    }


def adapter_columns(conn: psycopg.Connection[Any]) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    """The (column, type) lists of the adapter's temporary tables in this session, and the
    database's reason for each one that cannot be described."""
    found: dict[str, list[tuple[str, str]]] = {}
    reasons: dict[str, str] = {}
    for table in ADAPTER_TABLES:
        try:
            described = _rows(conn, f"DESCRIBE temp.main.{_ident(table)}")
        except psycopg.OperationalError:
            raise
        except psycopg.Error as exc:
            reasons[table] = _first_line(exc)
            continue
        found[table] = [(str(row[0]), str(row[1])) for row in described]
    return found, reasons


def check_adapter(conn: psycopg.Connection[Any], adapter: str) -> None:
    """Stop with a clear message when the adapter's tables break the contract in ADAPTER_TABLES."""
    if problems := adapter_problems(*adapter_columns(conn)):
        raise DeriveError(f"sql/adapters/{adapter}.sql breaks the adapter contract: " + "; ".join(problems))


def drop_role_views(conn: psycopg.Connection[Any], slm: str) -> list[str]:
    """Drop the per-Role view schemas of `slm` (T22, `make access`); return the ones dropped.

    Their views block CREATE OR REPLACE TABLE on the tables a run rebuilds (DependentObjectsStillExist
    on SereneDB 26.10.1), and their inverted indexes are snapshots that would go stale, so they go
    first and `make access` re-creates them. The LOGIN roles stay; they own nothing.
    """
    dropped = []
    for role in ROLES:
        schema = role_schema(slm, role)
        row = conn.execute(
            "SELECT count(*) FROM information_schema.schemata WHERE schema_name = %s", [schema]
        ).fetchone()
        if row and row[0]:
            conn.execute(f"DROP SCHEMA {_quoted(schema)} CASCADE")
            dropped.append(schema)
    return dropped


def drop_adapter_relations(conn: psycopg.Connection[Any], slm: str) -> None:
    """Drop this session's temporary adapter tables, and the persistent adapter views that derives
    before T4's review left in `slm` (they block every reload of the raw tables)."""
    for table in ADAPTER_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS temp.main.{_ident(table)}")
        conn.execute(f"DROP VIEW IF EXISTS {_quoted(slm)}.{_ident(table)}")


def report(conn: psycopg.Connection[Any], slm: str) -> list[str]:
    """Row counts and coverage of the derived columns, one line each."""
    s = _quoted(slm)
    lines = []
    for table in ("bgl_entry", "os_entry", "os_instance", "ownership"):
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
    # A warning, not a failure: T6 reports unmapped Components as "(unmapped)" by design, and the
    # organisers' Ownership Table may not cover every Component.
    lines.append(
        "Components without a Team: none"
        if not unmapped
        else "WARNING: Components without a Team: " + ", ".join(f"{d}/{c}" for d, c in unmapped)
    )
    # Where the Teams come from, so a complete mapping is not read as the organisers' mapping.
    sources = _rows(conn, f"SELECT team_source, count(*) FROM {s}.ownership GROUP BY 1 ORDER BY 1")
    lines.append("Ownership Table team_source: " + ", ".join(f"{src} {count:,}" for src, count in sources))
    if any(src == OWNERSHIP_STUB_SOURCE for src, _ in sources):
        lines.append(f"NOTE: {OWNERSHIP_NOTE}")
    return lines


def result_problems(conn: psycopg.Connection[Any], slm: str) -> list[str]:
    """Why the derived schema is not usable: an empty entry table or a missing index."""
    problems = []
    for table in ENTRY_INDEXES:
        (count,) = _rows(conn, f"SELECT count(*) FROM {_quoted(slm)}.{_ident(table)}")[0]
        if count == 0:
            problems.append(
                f"{slm}.{table} is empty; do the raw tables in SLM_RAW_SCHEMA hold the loaded data?"
            )
    indexes = {
        name for (name,) in _rows(conn, "SELECT indexname FROM pg_indexes WHERE schemaname = %s", [slm])
    }
    problems += [f"index {slm}.{index} is missing" for index in REQUIRED_INDEXES if index not in indexes]
    return problems


def failure(exc: Exception, slm: str, *, began: bool) -> tuple[str, int]:
    """The message and exit code for an error that stopped the derive step.

    Only a lost or refused connection is worth retrying (exit 1): the server restarts after a
    crash, and every step is re-runnable. Every other database error is deterministic (exit 2):
    running again gives the same error, so the message names the step and says so.
    """
    half_built = f" Schema {slm} is half-built until a derive completes." if began else ""
    cause = exc.cause if isinstance(exc, StepFailed) else exc
    if isinstance(cause, psycopg.OperationalError):
        where = f" during {exc.step}" if isinstance(exc, StepFailed) else ""
        return (
            f"derive: lost the database connection{where} ({_first_line(cause)}). Every step is "
            f"re-runnable: once the database is up, run `make derive` again.{half_built}",
            1,
        )
    if isinstance(exc, StepFailed):
        return (
            f"derive: {exc}. This is not a lost connection, so running it unchanged gives the same "
            f"error; fix the cause first.{half_built}",
            2,
        )
    if isinstance(exc, psycopg.Error):
        return f"derive: {type(exc).__name__}: {_first_line(exc)}.{half_built}", 2
    return f"derive: {exc}.{half_built}" if half_built else f"derive: {exc}", 2


# ----------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="derive.py",
        description="Build the canonical tables, the stub Ownership Table and the inverted indexes.",
    )
    parser.add_argument("--adapter", default="dev", help="sql/adapters/<adapter>.sql (default: dev)")
    parser.add_argument("--schema", help="target schema (default: SLM_SCHEMA, else slm)")
    parser.add_argument("--raw-schema", help="schema of the loader's raw tables (default: SLM_RAW_SCHEMA)")
    parser.add_argument(
        "--only",
        action="append",
        metavar="STEP",
        help="run only this sql/derive step, for example 20_patterns (repeatable; no adapter, no seeds)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    slm, began = "", False
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
        only = select_steps(files, args.only) if args.only else None
        if only is None and not split_statements(files[0].read_text()):
            raise DeriveError(
                f"{files[0].relative_to(SQL_DIR.parent)} has no statements yet: TODO(organiser), "
                "it is filled in when the organisers' loader arrives (T25)"
            )
        # A run without the Pattern step renders ${pattern_tier1} nowhere, so it needs no files.
        tier1 = (
            load_tier1(settings.pattern_tier1, settings.data_dir)
            if needs_tier1(files if only is None else only)
            else None
        )
        started = time.perf_counter()
        with psycopg.connect(
            settings.database_url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S
        ) as conn:
            row = conn.execute("SELECT version()").fetchone()
            version = serenedb_version(str(row[0]) if row else "")
            print(f"SereneDB {'.'.join(map(str, version))}: deriving {raw} -> {slm} ({args.adapter})")
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {slm_q}")
            values = variables(slm, raw, version, tier1)
            if dropped := drop_role_views(conn, slm):
                print(
                    f"Dropped the per-Role views in {', '.join(dropped)}: they depend on the tables this "
                    "run rebuilds. Run `make access` again afterwards."
                )
            began = True
            if only is not None:
                run(conn, only, values)
            else:
                drop_adapter_relations(conn, slm)
                run(conn, files[:1], values)
                check_adapter(conn, args.adapter)
                run(conn, files[1:], values)
                drop_adapter_relations(conn, slm)
            print(f"derive finished in {time.perf_counter() - started:.1f}s")
            for line in report(conn, slm):
                print(line)
            if problems := result_problems(conn, slm):
                raise DeriveError("the derived schema is not usable: " + "; ".join(problems))
    except (DeriveError, ConfigError, psycopg.Error) as exc:
        message, code = failure(exc, slm, began=began)
        print(message, file=sys.stderr)
        return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
